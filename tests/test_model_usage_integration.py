"""Opt-in PostgreSQL checks in a disposable, uniquely named test schema only."""
import importlib
import os
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.app.config import required_env
from backend.app.db.model_usage_models import ModelUsageEvent, ModelUsageScope
from backend.app.model_usage import ModelCall, parse_prices, usage_context
from backend.app.security.retrieval_scope import RetrievalScope

pytestmark = pytest.mark.integration


def test_postgres_migration_independent_commit_and_tenant_scope_constraint():
    if os.getenv("RUN_MODEL_USAGE_INTEGRATION_TESTS") != "1":
        pytest.skip("Model usage PostgreSQL integration tests not enabled")
    schema = "model_usage_test_" + uuid4().hex
    assert re.fullmatch(r"model_usage_test_[0-9a-f]{32}", schema)
    database_url = required_env("DATABASE_URL")
    control = create_engine(database_url)
    engine = create_engine(database_url, connect_args={"options": f"-csearch_path={schema}"})
    migration = importlib.import_module("migrations.versions.c9e1f3b5d708_model_usage_ledger")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    created = False
    try:
        with control.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            connection.execute(text("CREATE TABLE business_probe (id integer PRIMARY KEY)"))
        scope = RetrievalScope(uuid4(), uuid4())
        settings = SimpleNamespace(model_usage_prices=parse_prices(
            '{"dashscope:test":{"version":"example-test","currency":"CNY","total_per_million":"1"}}'))
        with factory() as business:
            business.execute(text("INSERT INTO business_probe VALUES (1)"))
            with usage_context(scope=scope, session=business, settings=settings) as context:
                with ModelCall("rerank", "test") as call:
                    with factory() as reader:
                        assert reader.get(ModelUsageEvent, call.id).status == "pending"
                    call.observe(dict(total_tokens=123))
            business.rollback()
        assert context.events[0]["persisted"]
        with factory() as reader:
            event = reader.get(ModelUsageEvent, call.id)
            assert event.status == "success" and event.total_tokens == 123
            assert str(event.estimated_cost) == "0.0001230000"
            assert reader.scalar(text("SELECT count(*) FROM business_probe")) == 0
            assert reader.scalar(select(ModelUsageScope.event_id)) == call.id
            with pytest.raises(IntegrityError):
                with reader.begin_nested():
                    reader.add(ModelUsageScope(event_id=call.id, tenant_id=uuid4(), knowledge_base_id=uuid4()))
                    reader.flush()
        # Exercise downgrade/upgrade only within the isolated test schema.
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                migration.upgrade()
        with factory() as reader:
            assert reader.scalar(select(ModelUsageEvent.id)) is None
    finally:
        engine.dispose()
        if created:
            # Exact name was generated and validated above; no user schema is touched.
            with control.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()
