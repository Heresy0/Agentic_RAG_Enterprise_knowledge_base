"""The M4 HTTP review/Worker suite on a fully migrated independent PostgreSQL DB."""
import pytest
from test_manufacturing_m3_postgres import pg_context
from test_manufacturing_m4 import (m4,
    test_business_only_review_export_and_worker_three_reads_per_case as round_trip,
    test_old_dataset_stops_on_active_fixture_source_rule_or_permission_change as stale_input,
    test_queue_claim_rechecks_business_authorization_before_model as queued_authorization,
    test_job_result_and_human_review_recheck_current_business_scope as result_authorization,
    test_date_only_clock_question_projects_known_date_and_limitation_without_model as precision_case)
from test_manufacturing_m4_matrix import test_formal_http_faults_stop_delivery_preserve_usage_and_have_positive_control as fault_check

pytestmark = pytest.mark.integration


@pytest.fixture
def api_context(monkeypatch):
    context = pg_context(monkeypatch, phase="m4")
    h = next(context)
    try:
        # M4 configures its own sources, docs and explicit review grant.
        with h.c.session_factory() as session:
            from sqlalchemy import delete
            from backend.app.db.models import KnowledgeDocument
            from backend.app.db.business_models import BusinessScopeGrant
            session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.tenant_id == h.c.tenant_id))
            session.execute(delete(BusinessScopeGrant).where(BusinessScopeGrant.tenant_id == h.c.tenant_id))
            session.commit()
        yield h.c
    finally:
        try: next(context)
        except StopIteration: pass


def test_postgres_business_review_dataset_worker_atomic_round_trip(m4):
    round_trip(m4)


@pytest.mark.parametrize("change", ["registry", "source", "rule", "review", "consent"])
def test_postgres_business_dataset_invalidation(m4, change):
    stale_input(m4, change)


def test_postgres_queued_task_revocation(m4):
    queued_authorization(m4)


def test_postgres_result_and_review_revocation(m4):
    result_authorization(m4)


@pytest.mark.parametrize('case,status',[('after_revision',409),('final_failure',503)])
def test_postgres_fault_matrix_keeps_independent_usage(m4,request,case,status):
    fault_check(m4,case,status,1,request)


@pytest.mark.parametrize('question',['工单实际开工日期是哪天，能给出几点几分？','实际开工时间是否精确到时分？'])
def test_postgres_precision_projection_keeps_snapshot_business_scope_without_model_usage(m4,question):
    from sqlalchemy import select,func
    from backend.app.db.business_models import QARunBusinessScope
    from backend.app.db.model_usage_models import ModelUsageEvent
    precision_case(m4,question)
    with m4.c.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(QARunBusinessScope).where(QARunBusinessScope.tenant_id==m4.c.tenant_id))==1
        assert session.scalar(select(func.count()).select_from(ModelUsageEvent).where(ModelUsageEvent.tenant_id==m4.c.tenant_id))==0
