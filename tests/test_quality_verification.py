"""End-to-end evaluation, human review and verified closure without provider calls."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from test_qa_feedback import data, put
from test_quality_review import opened, review_body, version
from backend.app.db.models import KnowledgeBaseUserGrant
from backend.app.db.review_models import QualityCase
from backend.app.db.verification_models import QualityEvaluationJob as Job, QualityEvaluationReview, QualityImprovement, QualityImprovementEvent
from backend.app.knowledge.answer_models import AnswerTimings
from backend.app.model_usage import ModelCall, parse_prices
from backend.app.quality.eval_budget import EvaluationBudget, EvaluationStopped, evaluation_budget
from backend.app.quality.evaluation_worker import QualityEvaluationWorker


@pytest.fixture
def cohort(data):
    cases, datasets = [], []
    for question in ("固定问题", "反馈问题"):
        case = opened(data, question=question)
        cid = case["case"]["id"]
        response = data.client.put(f"/api/quality/cases/{cid}", json=review_body(evaluation_question=question))
        assert response.status_code == 200, response.text
        cases.append(cid); datasets.append(version(data, [cid])["id"])
    settings = data.client.app.state.settings
    settings.model_usage_prices = parse_prices('{"dashscope:test-model":{"version":"test","currency":"CNY","total_per_million":"1"}}')
    factory = sessionmaker(bind=data.session.get_bind(), expire_on_commit=False)
    original = data.client.app.state.answer_service

    class Answer:
        def __init__(self, broken=False, tokens=1000, latency=10, hook=None):
            self.broken, self.tokens, self.latency, self.hook, self.calls = broken, tokens, latency, hook, 0
        def answer(self, question, *, scope, session):
            with ModelCall("answer_generation", "test-model") as call:
                self.calls += 1
                call.observe({"total_tokens": self.tokens} if self.tokens is not None else None)
                result = original.answer(question, scope=scope, session=session)
            if self.hook: self.hook()
            updates = dict(answer="应核对例外条件。", timings=AnswerTimings(search_total_ms=1,context_build_ms=1,total_ms=self.latency))
            if self.broken and question == getattr(self,"bad_question","反馈问题"): updates.update(answer="资料不足。", answerable=False, citations=[])
            return result.model_copy(update=updates)

    yield SimpleNamespace(data=data, cases=cases, datasets=datasets, factory=factory, settings=settings, Answer=Answer)


def enqueue(cohort, **extra):
    payload = dict(fixed_dataset_id=cohort.datasets[0], feedback_dataset_id=cohort.datasets[1], allow_model_calls=True)
    payload.update(extra)
    return cohort.data.client.post("/api/quality/evaluations", json=payload)


def run_job(cohort, answer=None, **extra):
    response = enqueue(cohort, **extra); assert response.status_code == 201, response.text
    job_id = response.json()["id"]
    worker = QualityEvaluationWorker(cohort.factory, cohort.settings, answer or cohort.Answer())
    assert worker.run_once()
    detail = cohort.data.client.get(f"/api/quality/evaluations/{job_id}")
    assert detail.status_code == 200, detail.text
    return detail.json()


def pair(cohort):
    baseline = run_job(cohort, cohort.Answer(broken=True))
    candidate = run_job(cohort)
    assert baseline["status"] == candidate["status"] == "succeeded"
    return baseline, candidate


def human_pass(cohort, job):
    for record in job["records"]:
        response = cohort.data.client.post(f"/api/quality/evaluations/{job['id']}/reviews/{record['sample']['id']}", json=dict(
            response_binding=record["response_binding"], verdict="passed", reason="已核对例外条件、原文和逐项引用。"))
        assert response.status_code == 200, response.text


def improvement(cohort):
    response = cohort.data.client.post("/api/quality/improvements", json=dict(case_id=cohort.cases[1], description="修复漏答例外条件",change_ref="commit:example"))
    assert response.status_code == 201, response.text
    return response.json()


def verify(cohort, row, baseline, candidate, **extra):
    return cohort.data.client.post(f"/api/quality/improvements/{row['id']}/verify", json=dict(
        expected_revision=row["revision"], baseline_job_id=baseline["id"], candidate_job_id=candidate["id"], note="核对两组结果与预算。", **extra))


def test_worker_real_pipeline_scoring_human_review_and_verified_close(cohort):
    baseline, candidate = pair(cohort)
    assert baseline["summary"]["partitions"]["feedback"]["proxy_passed"] == 0
    assert candidate["summary"]["partitions"]["feedback"]["proxy_passed"] == 1
    assert candidate["summary"]["usage"]["calls"] == 2
    row = improvement(cohort)
    assert verify(cohort, row, baseline, candidate).status_code == 409
    human_pass(cohort, candidate)
    checked = verify(cohort, row, baseline, candidate)
    assert checked.status_code == 200, checked.text
    checked = checked.json()
    closed = cohort.data.client.post(f"/api/quality/improvements/{row['id']}/close", json=dict(expected_revision=checked["revision"], note="候选结果与检查依据仍有效。"))
    assert closed.status_code == 200 and closed.json()["status"] == "closed"
    detail = cohort.data.client.get(f"/api/quality/improvements/{row['id']}").json()
    assert len(detail["history"]) == 3


@pytest.mark.parametrize("extra", [dict(allow_model_calls=False),dict(max_model_calls=0),dict(max_seconds=0),dict(max_tokens=1000)])
def test_evaluation_requires_explicit_budgeted_calls(cohort, extra):
    assert enqueue(cohort, **extra).status_code == 422


def test_one_active_job_per_tenant_and_queued_cancel(cohort):
    first = enqueue(cohort); assert first.status_code == 201
    assert enqueue(cohort).status_code == 409
    assert cohort.data.client.post(f"/api/quality/evaluations/{first.json()['id']}/cancel").status_code == 200
    assert enqueue(cohort).status_code == 201


def test_overlapping_groups_cannot_be_used_as_fixed_and_feedback(cohort):
    from backend.app.db.review_models import QualityDatasetVersion
    with cohort.factory() as session:
        source = session.get(QualityDatasetVersion, UUID(cohort.datasets[0]))
        copy = QualityDatasetVersion(tenant_id=source.tenant_id,name="copy",created_by_user_id=source.created_by_user_id,manifest_json=source.manifest_json)
        session.add(copy); session.commit(); duplicate = str(copy.id)
    assert enqueue(cohort, feedback_dataset_id=duplicate).status_code == 409


@pytest.mark.parametrize("limits", [dict(max_model_calls=1),dict(max_tokens=16000)])
def test_budget_stops_before_second_paid_call(cohort, limits):
    answer = cohort.Answer()
    job = run_job(cohort, answer, **limits)
    assert answer.calls == 1 and job["status"] == "failed"
    assert job["summary"]["budget"]["limited_by"] in {"calls","tokens"}
    assert len(job["records"]) == 1


def test_unknown_tokens_use_reservation_and_unknown_cost_needs_acknowledgement(cohort):
    cohort.settings.model_usage_prices = {}
    baseline = run_job(cohort, cohort.Answer(broken=True, tokens=None))
    candidate = run_job(cohort, cohort.Answer(tokens=None))
    assert candidate["summary"]["budget"]["charged_tokens"] == 32000
    human_pass(cohort, candidate)
    row = improvement(cohort)
    assert verify(cohort, row, baseline, candidate).status_code == 409
    assert verify(cohort, row, baseline, candidate, acknowledge_unknown_usage=True).status_code == 200


def test_human_review_binding_cannot_be_forged(cohort):
    job = run_job(cohort); sample = job["records"][0]["sample"]["id"]
    response = cohort.data.client.post(f"/api/quality/evaluations/{job['id']}/reviews/{sample}", json=dict(response_binding="0"*64,verdict="passed",reason="note"))
    assert response.status_code == 409


def test_uncertain_or_new_failed_review_blocks_closure(cohort):
    baseline, candidate = pair(cohort); human_pass(cohort,candidate); row=improvement(cohort)
    checked = verify(cohort,row,baseline,candidate); assert checked.status_code == 200
    record = candidate["records"][0]
    review = cohort.data.client.post(f"/api/quality/evaluations/{candidate['id']}/reviews/{record['sample']['id']}",json=dict(response_binding=record["response_binding"],verdict="uncertain",reason="需进一步核对"))
    assert review.status_code == 200
    assert cohort.data.client.post(f"/api/quality/improvements/{row['id']}/close",json=dict(expected_revision=2,note="close")).status_code == 409


@pytest.mark.parametrize("kind", ["latency","tokens"])
def test_growth_limits_block_release(cohort, kind):
    baseline = run_job(cohort,cohort.Answer(broken=True))
    candidate = run_job(cohort,cohort.Answer(latency=100 if kind=="latency" else 10,tokens=5000 if kind=="tokens" else 1000))
    human_pass(cohort,candidate)
    assert verify(cohort,improvement(cohort),baseline,candidate).status_code == 409


@pytest.mark.parametrize("who", ["owner","peer","partial_admin","outside"])
def test_jobs_and_improvements_enforce_tenant_and_current_admin_scope(cohort, who):
    job = run_job(cohort); row=improvement(cohort); d=cohort.data
    # Single-KB admin is sufficient here; remove its grant explicitly for this case.
    if who == "partial_admin":
        grant=d.session.get(KnowledgeBaseUserGrant,(d.current.tenant_id,d.bases[0].id,d.principals[2].user_id));d.session.delete(grant);d.session.commit()
    d.current={"owner":d.principals[0],"peer":d.principals[1],"partial_admin":d.principals[2],"outside":d.outside}[who]
    assert d.client.get(f"/api/quality/evaluations/{job['id']}").status_code == 404
    assert d.client.get(f"/api/quality/improvements/{row['id']}").status_code == 404
    assert d.client.get("/api/quality/evaluations").json()["items"] == []
    assert enqueue(cohort).status_code == 404


def test_revoke_export_before_worker_prevents_model_calls(cohort):
    response=enqueue(cohort); job_id=UUID(response.json()["id"]);d=cohort.data
    run=d.session.get(QualityCase,UUID(cohort.cases[1])).run_id
    d.current=d.principals[0];put(d,run,share_with_admins=True,allow_dataset_export=False);d.current=d.principals[3]
    answer=cohort.Answer();QualityEvaluationWorker(cohort.factory,cohort.settings,answer).run_once()
    assert answer.calls == 0
    with cohort.factory() as session: assert session.get(Job,job_id).status == "failed"


def test_cancel_inflight_fences_old_worker_and_preserves_cancelled_status(cohort):
    response=enqueue(cohort);job_id=UUID(response.json()["id"])
    def cancel():
        with cohort.factory() as session:
            row=session.get(Job,job_id);row.status="cancelled";row.active_slot=None;row.lease_token=None;row.lease_expires_at=None;session.commit()
    answer=cohort.Answer(hook=cancel);QualityEvaluationWorker(cohort.factory,cohort.settings,answer).run_once()
    assert answer.calls == 1
    with cohort.factory() as session: assert session.get(Job,job_id).status == "cancelled"


def test_expired_worker_is_failed_without_automatic_retry(cohort):
    response=enqueue(cohort);worker=QualityEvaluationWorker(cohort.factory,cohort.settings,cohort.Answer())
    job_id,token,limits=worker.claim()
    with cohort.factory() as session:
        row=session.get(Job,job_id);row.lease_expires_at=datetime.now(timezone.utc)-timedelta(seconds=1);session.commit()
    worker.recover_stale()
    with pytest.raises(EvaluationStopped):worker.progress(job_id,token,[],EvaluationBudget(limits),final=True)
    with cohort.factory() as session:assert session.get(Job,job_id).status == "failed"
    assert worker.run_once() is False


def test_budget_scope_does_not_affect_normal_model_calls_and_checks_deadline():
    limits=dict(max_model_calls=1,max_tokens=2000,reservation_tokens=1000,max_seconds=10)
    budget=EvaluationBudget(limits)
    with evaluation_budget(budget):
        with ModelCall("answer_generation","fake") as call:call.observe({"total_tokens":100})
        with pytest.raises(EvaluationStopped):
            with ModelCall("answer_generation","fake"):pytest.fail("call should be blocked")
    with ModelCall("answer_generation","fake") as call:call.observe({"total_tokens":100})
    timeout=EvaluationBudget(limits);timeout.started-=20
    with pytest.raises(EvaluationStopped):timeout.before_call()


def test_fixed_regression_blocks_even_if_feedback_is_fixed(cohort):
    baseline=run_job(cohort,cohort.Answer(broken=True))
    answer=cohort.Answer(broken=True);answer.bad_question="固定问题"
    candidate=run_job(cohort,answer);human_pass(cohort,candidate)
    assert verify(cohort,improvement(cohort),baseline,candidate).status_code == 409


def test_unknown_price_acknowledgement_does_not_waive_known_token_growth(cohort):
    cohort.settings.model_usage_prices={}
    baseline=run_job(cohort,cohort.Answer(broken=True))
    candidate=run_job(cohort,cohort.Answer(tokens=5000));human_pass(cohort,candidate)
    result=verify(cohort,improvement(cohort),baseline,candidate,acknowledge_unknown_usage=True)
    assert result.status_code == 409 and "token_growth" in result.text


def test_runtime_change_invalidates_previous_verification(cohort,monkeypatch):
    from backend.app.quality import verification
    baseline,candidate=pair(cohort);human_pass(cohort,candidate);row=improvement(cohort)
    assert verify(cohort,row,baseline,candidate).status_code == 200
    monkeypatch.setattr(verification,"runtime_config",lambda _:dict(fingerprint="changed",values={}))
    assert cohort.data.client.post(f"/api/quality/improvements/{row['id']}/close",json=dict(expected_revision=2,note="close")).status_code == 409


def test_updated_improvement_reopens_and_stale_revision_is_rejected(cohort):
    row=improvement(cohort)
    payload=dict(case_id=cohort.cases[1],expected_revision=1,description="补充修复说明",change_ref="commit:new")
    response=cohort.data.client.put(f"/api/quality/improvements/{row['id']}",json=payload)
    assert response.status_code == 200 and response.json()["revision"] == 2
    assert cohort.data.client.put(f"/api/quality/improvements/{row['id']}",json=payload).status_code == 409


def test_different_role_inputs_cannot_be_compared(cohort):
    baseline=run_job(cohort)
    candidate=run_job(cohort,fixed_dataset_id=cohort.datasets[1],feedback_dataset_id=cohort.datasets[0])
    response=cohort.data.client.get("/api/quality/comparison",params=dict(baseline_job_id=baseline["id"],candidate_job_id=candidate["id"]))
    assert response.status_code == 409


def test_queue_approval_expires_without_spending(cohort):
    response=enqueue(cohort);job_id=UUID(response.json()["id"])
    with cohort.factory() as session:
        row=session.get(Job,job_id);row.created_at=datetime.now(timezone.utc)-timedelta(hours=2);session.commit()
    answer=cohort.Answer();QualityEvaluationWorker(cohort.factory,cohort.settings,answer).run_once()
    assert answer.calls == 0
    with cohort.factory() as session:assert session.get(Job,job_id).status == "failed"


def test_actual_tokens_above_reservation_stop_subsequent_calls():
    budget=EvaluationBudget(dict(max_model_calls=5,max_tokens=2000,reservation_tokens=1000,max_seconds=10))
    with evaluation_budget(budget):
        with ModelCall("answer_generation","fake") as call:call.observe({"total_tokens":2500})
        with pytest.raises(EvaluationStopped):budget.check()
    assert budget.charged_tokens == 2500 and budget.calls == 1


def test_knowledge_fix_can_verify_original_issue_using_new_regression_sample(cohort):
    d=cohort.data
    old=opened(d,question="原始知识缺失问题")
    body=review_body(sample_status="draft",root_cause="knowledge_missing",expected_answerable=None,reference_answer=None,required_facts=[],evidence=[])
    assert d.client.put(f"/api/quality/cases/{old['case']['id']}",json=body).status_code == 200
    response=d.client.post("/api/quality/improvements",json=dict(case_id=old["case"]["id"],regression_case_id=cohort.cases[1],description="补充资料后关联新快照",change_ref="document:v2"))
    assert response.status_code == 201
    row=response.json();baseline,candidate=pair(cohort);human_pass(cohort,candidate)
    checked=verify(cohort,row,baseline,candidate)
    assert checked.status_code == 200,checked.text
    closed=d.client.post(f"/api/quality/improvements/{row['id']}/close",json=dict(expected_revision=2,note="以新样本验证原问题"))
    assert closed.status_code == 200
    assert closed.json()["case_id"] == old["case"]["id"] and closed.json()["regression_case_id"] == cohort.cases[1]


def test_relinking_sample_does_not_broaden_historical_note_access(cohort):
    d=cohort.data
    response=d.client.post("/api/quality/improvements",json=dict(case_id=cohort.cases[1],regression_case_id=cohort.cases[0],description="旧样本依据",change_ref="old"))
    row=response.json()
    changed=d.client.put(f"/api/quality/improvements/{row['id']}",json=dict(case_id=cohort.cases[1],regression_case_id=cohort.cases[1],expected_revision=1,description="换用新样本",change_ref="new"))
    assert changed.status_code == 200
    old_run=d.session.get(QualityCase,UUID(cohort.cases[0])).run_id
    d.current=d.principals[0];put(d,old_run,share_with_admins=False,allow_dataset_export=False);d.current=d.principals[3]
    assert d.client.get(f"/api/quality/improvements/{row['id']}").status_code == 404


def test_missing_fixed_observation_is_not_waived_by_human_pass(cohort):
    from backend.app.quality.capture import _current
    class Incomplete(cohort.Answer):
        def answer(self,question,**kwargs):
            result=super().answer(question,**kwargs)
            if question=="固定问题":_current.get().incomplete=True
            return result
    baseline=run_job(cohort,Incomplete(broken=True));candidate=run_job(cohort,Incomplete())
    human_pass(cohort,candidate)
    response=verify(cohort,improvement(cohort),baseline,candidate)
    assert response.status_code == 409 and "candidate_observation_incomplete" in response.text
