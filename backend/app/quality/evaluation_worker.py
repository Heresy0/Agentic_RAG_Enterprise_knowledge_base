"""Fenced, non-retrying evaluation worker; no JWTs or commands are stored in jobs."""
from datetime import datetime, timedelta, timezone
import math
from time import perf_counter
from uuid import UUID, uuid4

from sqlalchemy import select
from ..audit_service import AuditService

from ..db.models import KnowledgeBase, User
from ..db.verification_models import QualityEvaluationJob as Job
from ..evaluation.metrics import score_answer, normalize
from ..model_usage import usage_context, summarize_usage
from ..security.principal import Principal
from ..security.retrieval_scope import RetrievalScope
from .capture import capture_snapshot
from .eval_budget import EvaluationBudget, EvaluationStopped, evaluation_budget
from .reviews import ReviewConflict, digest, utc
from .service import ObservationNotFound
from .verification import VerificationStore, runtime_config


def summarize(records, budget):
    latencies = sorted(row["latency_ms"] for row in records)
    usage = summarize_usage([event for row in records for event in row["usage_events"]])
    partitions = {}
    for role in ("fixed", "feedback"):
        selected = [row for row in records if row["sample"]["role"] == role]
        partitions[role] = dict(total=len(selected), proxy_passed=sum(row["proxy_pass"] for row in selected),
            false_refusals=sum(row["sample"]["answerable"] and not row["response"]["answerable"] for row in selected),
            wrong_accepts=sum(not row["sample"]["answerable"] and row["response"]["answerable"] for row in selected))
    return dict(partitions=partitions, p95_ms=latencies[math.ceil(len(latencies) * .95) - 1] if latencies else None,
        usage=usage, budget=budget.snapshot(), limitation="本地服务单次运行；字面代理须结合人工复核。失败任务用量为已完成记录小计，完整账本按 job_id 查询。")


class QualityEvaluationWorker:
    def __init__(self, session_factory, settings, answer_service):
        self.factory, self.settings, self.answer = session_factory, settings, answer_service

    def principal(self, session, job):
        user = session.scalar(select(User).where(User.id == job.requested_by_user_id,
            User.tenant_id == job.tenant_id, User.status == "active"))
        if user is None: raise ObservationNotFound()
        return Principal(user.id, user.tenant_id, user.external_subject)

    def active(self, session, job_id, token):
        job = session.scalar(select(Job).where(Job.id == job_id).with_for_update().execution_options(populate_existing=True))
        if (job is None or job.status != "running" or job.lease_token != token or
                job.lease_expires_at is None or utc(job.lease_expires_at) <= datetime.now(timezone.utc)):
            raise EvaluationStopped("lease_lost_or_cancelled")
        return job

    def guard(self, job_id, token, sample=None):
        with self.factory() as session:
            job = self.active(session, job_id, token)
            principal = self.principal(session, job)
            store = VerificationStore(session, principal, self.settings)
            if sample is None:
                store.get_job(job.id, require_current=True)
            else:
                case, run, feedback = store.reviews.get_case(UUID(sample["id"][3:]))
                eligible, _ = store.reviews.eligibility(case, run, feedback)
                if (not eligible or case.revision != sample["provenance"]["case_revision"] or
                        case.source_binding != sample["provenance"]["source_binding"]):
                    raise ReviewConflict("评测样本或权限已变化。")
            job.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=180)
            session.commit()

    def recover_stale(self):
        with self.factory() as session:
            rows = session.scalars(select(Job).where(Job.status == "running",
                Job.lease_expires_at <= datetime.now(timezone.utc)).with_for_update(skip_locked=True))
            for job in rows:
                job.status, job.active_slot, job.error_type = "failed", None, "worker_lease_expired"
                job.lease_token, job.lease_expires_at = None, None
                job.finished_at = datetime.now(timezone.utc)
            session.commit()

    def claim(self):
        with self.factory() as session:
            job = session.scalar(select(Job).where(Job.status == "queued").order_by(Job.created_at, Job.id)
                                 .with_for_update(skip_locked=True).limit(1))
            if job is None: return None
            job.status, job.lease_token = "running", uuid4().hex
            job.started_at = datetime.now(timezone.utc)
            job.lease_expires_at = job.started_at + timedelta(seconds=180)
            job.runtime_json = runtime_config(self.settings)
            session.commit()
            return job.id, job.lease_token, dict(job.budget_json)

    def prepare(self, job_id, token):
        with self.factory() as session:
            job = self.active(session, job_id, token)
            if utc(job.started_at) - utc(job.created_at) > timedelta(hours=1):
                raise ReviewConflict("排队授权已过期，请重新创建任务。")
            principal = self.principal(session, job)
            store = VerificationStore(session, principal, self.settings)
            samples, inputs, _ = store.inputs(job.fixed_dataset_id, job.feedback_dataset_id)
            if inputs != job.inputs_json: raise ReviewConflict("任务输入指纹已变化。")
            return samples

    def evaluate(self, job_id, token, sample):
        self.guard(job_id, token, sample)
        with self.factory() as session:
            job = session.get(Job, job_id)
            principal = self.principal(session, job)
            ids = frozenset(UUID(value) for value in sample["source_groups"])
            names = tuple(session.execute(select(KnowledgeBase.id, KnowledgeBase.name).where(
                KnowledgeBase.tenant_id == job.tenant_id, KnowledgeBase.id.in_(ids))).all())
            scope = RetrievalScope(tenant_id=job.tenant_id, knowledge_base_ids=ids, knowledge_base_names=names)
            request_id = uuid4().hex
            started = perf_counter()
            with usage_context(scope=scope, session=session, settings=self.settings, request_id=request_id,
                               actor_user_id=principal.user_id, job_id=job.id) as usage, capture_snapshot(scope, sample["question"]) as capture:
                response = self.answer.answer(sample["question"], scope=scope, session=session)
            elapsed = (perf_counter() - started) * 1000
            reported = response.timings.total_ms if response.timings is not None else elapsed
            latency = reported if math.isfinite(reported) and reported >= 0 else elapsed
            status, snapshot = capture.freeze()
            context = (snapshot.get("context") or {}).get("items", [])
            answer = response.model_dump(mode="json")
            metrics = score_answer(sample, answer, context)
            forbidden = [claim for claim in sample.get("forbidden_claims", []) if normalize(claim) in normalize(answer["answer"])]
            record = dict(sample=sample, request_id=request_id, response=answer, context=context,
                observations_complete=status == "captured", metrics=metrics, forbidden_matches=forbidden,
                proxy_pass=bool(metrics["automatic_proxy_pass"] and not forbidden and status == "captured"),
                latency_ms=latency, usage_events=usage.events)
            record["response_binding"] = digest(dict(sample=sample, response=answer, context=context,
                runtime=runtime_config(self.settings)))
            session.rollback()  # Evaluation never writes QA/conversation business state.
            return record

    def progress(self, job_id, token, records, budget, *, final=False, error=None):
        with self.factory() as session:
            job = self.active(session, job_id, token)
            job.records_json, job.summary_json = list(records), summarize(records, budget)
            if final:
                job.status, job.active_slot = ("failed" if error else "succeeded"), None
                job.error_type = error
                job.finished_at = datetime.now(timezone.utc)
                job.lease_token, job.lease_expires_at = None, None
                AuditService(session).add(tenant_id=job.tenant_id, actor_user_id=None,
                    action="quality.evaluation_finished", resource_type="quality", resource_id=job.id,
                    outcome="failed" if error else "success", details={"reason": error or "completed"})
            else:
                job.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=180)
            session.commit()

    def run_once(self):
        self.recover_stale()
        claimed = self.claim()
        if claimed is None: return False
        job_id, token, limits = claimed
        budget, records = EvaluationBudget(limits), []
        try:
            samples = self.prepare(job_id, token)
            if len(samples) > limits["max_cases"]: raise ReviewConflict("样本数量超过预算。")
            with evaluation_budget(budget):
                for sample in samples:
                    budget.check()
                    budget.guard = lambda sample=sample: self.guard(job_id, token, sample)
                    record = self.evaluate(job_id, token, sample)
                    budget.check()
                    records.append(record)
                    self.progress(job_id, token, records, budget)
            self.guard(job_id, token)
            self.progress(job_id, token, records, budget, final=True)
        except Exception as exc:
            # Errors never contain credentials, provider response bodies or arbitrary model text.
            try: self.progress(job_id, token, records, budget, final=True, error=type(exc).__name__)
            except EvaluationStopped: pass  # A cancelled/recovered job fences this worker out.
        return True
