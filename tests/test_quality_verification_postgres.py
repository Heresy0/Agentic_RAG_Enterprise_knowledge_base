"""Third-stage schema, active-slot uniqueness and fenced claims in isolated PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib
import os
import re
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import required_env
from backend.app.db.conversation_models import Conversation
from backend.app.db.models import Tenant, User, KnowledgeBase
from backend.app.db.verification_models import QualityEvaluationJob as Job, QualityEvaluationReview, QualityImprovement, QualityImprovementEvent
from backend.app.quality.eval_budget import EvaluationStopped, EvaluationBudget
from backend.app.quality.evaluation_worker import QualityEvaluationWorker

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('starting_revision', ['e1a3c5f7b902', 'f2b4d6e8a013', 'b6d8f0a21347'])
def test_runtime_upgrade_joins_legacy_memory_and_quality_without_data_loss(starting_revision):
    if os.getenv('RUN_QA_FEEDBACK_INTEGRATION_TESTS') != '1':
        pytest.skip('Quality PostgreSQL checks not enabled')
    schema = 'quality_runtime_upgrade_' + uuid4().hex
    assert re.fullmatch(r'quality_runtime_upgrade_[0-9a-f]{32}', schema)
    control = create_engine(required_env('DATABASE_URL'))
    engine = create_engine(required_env('DATABASE_URL'), connect_args={'options': f'-csearch_path={schema}'})
    script = ScriptDirectory.from_config(Config('alembic.ini'))
    assert script.get_current_head() == 'c7e9a1b32458'
    created = False
    try:
        with control.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        with engine.begin() as connection:
            for model in (Tenant, User, KnowledgeBase, Conversation):
                model.__table__.create(connection)
            context = MigrationContext.configure(connection, opts={
                'fn': lambda revisions, _: script._upgrade_revs('head', revisions)})
            with Operations.context(context):
                if starting_revision == 'f2b4d6e8a013':
                    script.get_revision(starting_revision).module.upgrade()
                elif starting_revision == 'b6d8f0a21347':
                    for revision in ('f4b6d8e0a215', 'a5c7e9f10236', 'b6d8f0a21347'):
                        script.get_revision(revision).module.upgrade()
                context.stamp(script, starting_revision)
                tenant_id, user_id, conversation_id = uuid4(), uuid4(), uuid4()
                connection.execute(Tenant.__table__.insert().values(id=tenant_id, name='runtime-preserved'))
                connection.execute(User.__table__.insert().values(id=user_id, tenant_id=tenant_id,
                                   external_subject=uuid4().hex, name='runtime-owner'))
                connection.execute(Conversation.__table__.insert().values(
                    id=conversation_id, tenant_id=tenant_id, user_id=user_id, title='existing conversation'))
                if starting_revision == 'f2b4d6e8a013':
                    connection.execute(text("UPDATE conversations SET memory_json = :value"),
                                       {'value': '{"summary":"preserve existing memory"}'})
                context.run_migrations()
                # A second upgrade is idempotent and follows the actual Alembic graph.
                context.run_migrations()
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == 'c7e9a1b32458'
            row = connection.execute(text('SELECT title, memory_json FROM conversations')).one()
            assert row.title == 'existing conversation'
            assert row.memory_json == ({'summary': 'preserve existing memory'}
                                       if starting_revision == 'f2b4d6e8a013' else None)
            assert inspect(connection).has_table('quality_evaluation_jobs')
            assert inspect(connection).has_table('quality_improvements')
    finally:
        engine.dispose()
        if created:
            with control.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()


def test_postgres_verification_migration_unique_active_job_and_fencing():
    if os.getenv("RUN_QA_FEEDBACK_INTEGRATION_TESTS") != "1": pytest.skip("Quality PostgreSQL checks not enabled")
    schema = "quality_verification_test_" + uuid4().hex
    assert re.fullmatch(r"quality_verification_test_[0-9a-f]{32}",schema)
    url = required_env("DATABASE_URL")
    control=create_engine(url);engine=create_engine(url,connect_args={"options":f"-csearch_path={schema}"})
    modules=[importlib.import_module("migrations.versions."+name) for name in (
        "f4b6d8e0a215_qa_snapshots_feedback","a5c7e9f10236_quality_reviews","b6d8f0a21347_quality_verification")]
    created=False
    try:
        with control.begin() as connection: connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created=True
        with engine.begin() as connection:
            for model in (Tenant,User,KnowledgeBase,Conversation):model.__table__.create(connection)
            with Operations.context(MigrationContext.configure(connection)):
                for migration in modules:migration.upgrade()
            for model in (Job,QualityEvaluationReview,QualityImprovement,QualityImprovementEvent):
                assert {c["name"] for c in inspect(connection).get_columns(model.__tablename__)}==set(model.__table__.columns.keys())
        factory=sessionmaker(bind=engine,expire_on_commit=False)
        tenant=uuid4()
        def queued(session):
            row=Job(tenant_id=tenant,requested_by_user_id=uuid4(),fixed_dataset_id=uuid4(),feedback_dataset_id=uuid4(),
                inputs_json={},budget_json=dict(max_seconds=100,max_cases=2,max_model_calls=2,max_tokens=32000,reservation_tokens=16000),
                runtime_json={},records_json=[],summary_json={})
            session.add(row);session.flush();return row
        with factory() as session:
            row=queued(session);job_id=row.id;session.commit()
            with pytest.raises(IntegrityError):
                with session.begin_nested():queued(session)
        barrier=Barrier(2)
        worker=QualityEvaluationWorker(factory,SimpleNamespace(),None)
        def claim(_):barrier.wait(timeout=10);return worker.claim()
        with ThreadPoolExecutor(max_workers=2) as pool:claimed=list(pool.map(claim,range(2)))
        assert sum(value is not None for value in claimed)==1
        job_id,token,limits=next(value for value in claimed if value is not None)
        with factory() as session:
            row=session.get(Job,job_id);row.lease_expires_at=datetime.now(timezone.utc)-timedelta(seconds=1);session.commit()
        worker.recover_stale()
        with pytest.raises(EvaluationStopped):worker.progress(job_id,token,[],EvaluationBudget(limits),final=True)
        with factory() as session:
            assert session.get(Job,job_id).status=="failed"
            queued(session);session.commit()
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                modules[-1].downgrade();modules[-1].upgrade()
            assert connection.scalar(text("SELECT count(*) FROM quality_evaluation_jobs"))==0
    finally:
        engine.dispose()
        if created:
            with control.begin() as connection:connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()
