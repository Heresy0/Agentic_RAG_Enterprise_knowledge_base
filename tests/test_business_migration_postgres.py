"""M1 migration checks require an explicitly named independent test database."""
import importlib
import os
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.models import Tenant, User, KnowledgeBase, AuditEvent
from backend.app.db.conversation_models import Conversation
from backend.app.db.quality_models import QARun
from backend.app.db.business_models import BusinessScopeGrant, ConversationBusinessBinding, QARunBusinessScope
from backend.app.manufacturing.grants import BusinessGrantConfig, apply_grants

pytestmark=pytest.mark.integration
MIGRATION=importlib.import_module("migrations.versions.d8f0b2c43569_business_work_order_access")
MODELS=(BusinessScopeGrant,ConversationBusinessBinding,QARunBusinessScope)


@pytest.fixture
def database():
    url=os.environ.get("MANUFACTURING_TEST_DATABASE_URL")
    if not url: pytest.skip("explicit independent M1 test database not configured")
    assert re.fullmatch(r"manufacturing_m1_test_[0-9a-f]{12}",make_url(url).database or ""),"refuse non-test database"
    schema="m1_"+uuid4().hex
    control=create_engine(url)
    engine=create_engine(url,connect_args={"options":f"-csearch_path={schema}"})
    created=False
    try:
        with control.begin() as connection: connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created=True
        with engine.begin() as connection:
            for model in (Tenant,User,KnowledgeBase,Conversation,QARun,AuditEvent): model.__table__.create(connection)
        yield engine
    finally:
        engine.dispose()
        if created:
            with control.begin() as connection: connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()


def seed(connection):
    tenant,user,conversation,run=[uuid4() for _ in range(4)]
    connection.execute(Tenant.__table__.insert().values(id=tenant,name="preserved tenant"))
    connection.execute(User.__table__.insert().values(id=user,tenant_id=tenant,external_subject=uuid4().hex,name="owner"))
    connection.execute(Conversation.__table__.insert().values(id=conversation,tenant_id=tenant,user_id=user,title="preserved conversation"))
    connection.execute(QARun.__table__.insert().values(id=run,tenant_id=tenant,user_id=user,conversation_id=conversation,
        request_id=uuid4().hex,question="preserved question",retrieval_question="preserved question",
        response_json={},snapshot_json={},snapshot_status="captured",config_json={}))
    return tenant,user,conversation,run


def test_alembic_graph_upgrade_matches_models_and_preserves_old_rows(database):
    script=ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_current_head()=="e9b2d4f60713"
    with database.begin() as connection:
        ids=seed(connection)
        context=MigrationContext.configure(connection,opts={"fn":lambda revisions,_:script._upgrade_revs("head",revisions)})
        with Operations.context(context):
            context.stamp(script,MIGRATION.down_revision)
            context.run_migrations();context.run_migrations()
        assert connection.scalar(text("SELECT version_num FROM alembic_version"))=="e9b2d4f60713"
        inspector=inspect(connection)
        for model in MODELS:
            assert {c["name"] for c in inspector.get_columns(model.__tablename__)}==set(model.__table__.columns.keys())
            assert {c["name"] for c in inspector.get_check_constraints(model.__tablename__)}=={
                c.name for c in model.__table__.constraints if c.__class__.__name__=="CheckConstraint"}
        assert connection.scalar(select(Conversation.title).where(Conversation.id==ids[2]))=="preserved conversation"
        assert connection.scalar(select(QARun.question).where(QARun.id==ids[3]))=="preserved question"


def test_postgres_composite_constraints_and_historical_scope_survive_revocation(database):
    with database.begin() as connection:
        tenant,user,conversation,run=seed(connection)
        other,foreign,_,foreign_run=seed(connection)
        with Operations.context(MigrationContext.configure(connection)): MIGRATION.upgrade()
        base=dict(tenant_id=tenant,user_id=user,source_system_id="mock-mes-hc",plant_code="HC-P01",permission="view")
        connection.execute(BusinessScopeGrant.__table__.insert().values(**base))
        for updates in ({},{"user_id":foreign},{"permission":"admin"},{"plant_code":""}):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(BusinessScopeGrant.__table__.insert().values(**(base|updates)))
        binding=dict(tenant_id=tenant,conversation_id=conversation,source_system_id="mock-mes-hc",
            plant_code="HC-P01",work_order_id="WO-017",source_revision="r1",payload_sha256="a"*64)
        connection.execute(ConversationBusinessBinding.__table__.insert().values(**binding))
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(ConversationBusinessBinding.__table__.insert().values(**(binding|{"tenant_id":other})))
        access=dict(tenant_id=tenant,run_id=run,source_system_id="mock-mes-hc",plant_code="HC-P01",work_order_id="WO-017")
        connection.execute(QARunBusinessScope.__table__.insert().values(**access))
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(QARunBusinessScope.__table__.insert().values(**(access|{"run_id":foreign_run})))
        connection.execute(BusinessScopeGrant.__table__.delete())
        assert connection.scalar(text("SELECT count(*) FROM qa_run_business_scopes"))==1


def test_explicit_test_only_downgrade_roundtrip_does_not_remove_parent_data(database):
    with database.begin() as connection:
        _,_,conversation,run=seed(connection)
        with Operations.context(MigrationContext.configure(connection)):
            MIGRATION.upgrade();MIGRATION.downgrade();MIGRATION.upgrade()
        assert connection.scalar(select(Conversation.title).where(Conversation.id==conversation))=="preserved conversation"
        assert connection.scalar(select(QARun.question).where(QARun.id==run))=="preserved question"


def test_nonempty_business_permissions_block_unsafe_downgrade(database):
    with database.begin() as connection:
        tenant,user,_,_=seed(connection)
        with Operations.context(MigrationContext.configure(connection)): MIGRATION.upgrade()
        connection.execute(BusinessScopeGrant.__table__.insert().values(tenant_id=tenant,user_id=user,
            source_system_id="mock-mes-hc",plant_code="HC-P01",permission="view"))
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError,match="拒绝直接降级"): MIGRATION.downgrade()
        assert all(inspect(connection).has_table(model.__tablename__) for model in MODELS)
        assert connection.scalar(text("SELECT count(*) FROM business_scope_grants"))==1


def test_concurrent_grants_are_idempotent_and_audited(database):
    with database.begin() as connection:
        tenant,user,_,_=seed(connection)
        subject=connection.scalar(select(User.external_subject).where(User.id==user))
        with Operations.context(MigrationContext.configure(connection)): MIGRATION.upgrade()
    config=BusinessGrantConfig.model_validate(dict(schema_version="business-grants-v1",tenant_id=str(tenant),
        source_system_id="mock-mes-hc",grants=[dict(user_id=str(user),external_subject=subject,
        plant_code="HC-P01",permissions=["view","review"])]))
    barrier=Barrier(2)
    def apply(_):
        with Session(database) as session,session.begin():
            barrier.wait(timeout=5)
            return apply_grants(session,config)["changed"]
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(apply,range(2)))
    assert sorted(results)==[0,2]
    with database.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM business_scope_grants"))==2
        assert connection.scalar(text("SELECT count(*) FROM audit_events"))==2
