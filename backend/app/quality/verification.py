"""Evaluation authorization, immutable observations and release-check decisions."""
import json
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID
from datetime import datetime, timezone
from functools import lru_cache
from importlib.metadata import version, PackageNotFoundError
import platform

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..db.models import User
from ..db.verification_models import QualityEvaluationJob as Job, QualityEvaluationReview as AnswerReview, QualityImprovement as Improvement, QualityImprovementEvent as ImprovementEvent
from ..evaluation.metrics import SCORER_VERSION
from ..security.principal import Principal
from .reviews import ReviewStore, ReviewConflict, digest, utc
from .service import ObservationNotFound, pipeline_fingerprint

VERIFICATION_SCORER = SCORER_VERSION + ":quality-verification-v1"


@lru_cache(maxsize=1)
def dependency_versions():
    result = {"python": platform.python_version()}
    for package in ("langchain-core", "langchain-openai", "openai", "pydantic", "SQLAlchemy", "requests"):
        try: result[package] = version(package)
        except PackageNotFoundError: result[package] = None
    return result


def runtime_config(settings):
    values = {key: getattr(settings, key, None) for key in (
        "chat_model", "embedding_model", "rerank_model", "agentic_rag_mode", "rag_planner_mode",
        "rag_feedback_enabled", "rag_repair_enabled", "rag_rerank_top_k", "rag_parent_context_enabled",
        "rag_context_selection_enabled", "rag_max_searches", "agent_max_decisions", "agent_max_searches",
        "agent_max_reads", "agent_max_evidence", "agent_max_prompt_bytes", "agent_max_output_tokens",
        "agent_max_decision_tokens", "agent_max_seconds", "agent_decision_timeout_seconds",
        "rag_max_optional_calls", "rag_max_optional_tokens", "rag_max_optional_seconds", "rag_planner_output_tokens",
        "rag_repair_output_tokens", "rag_repair_prompt_bytes", "rag_regeneration_output_tokens",
        "rag_context_selection_output_tokens", "rag_context_selection_prompt_bytes", "rag_planner_timeout_seconds",
        "rag_repair_timeout_seconds", "rag_regeneration_timeout_seconds", "rag_context_selection_timeout_seconds")}
    values["endpoint_hashes"] = {key: digest(getattr(settings, key, None)) for key in ("chat_base_url", "rerank_url")}
    values["pricing_hash"] = digest(json.dumps(getattr(settings, "model_usage_prices", {}), sort_keys=True, default=str))
    values.update(pipeline=pipeline_fingerprint(), scorer=VERIFICATION_SCORER, dependencies=dependency_versions())
    return dict(values=values, fingerprint=digest(values))


class VerificationStore:
    def __init__(self, session, principal, settings):
        self.session, self.principal, self.settings = session, principal, settings
        self.reviews = ReviewStore(session, principal)

    def inputs(self, fixed_id, feedback_id, *, require_current=True):
        samples, bindings, current = [], {}, True
        groups = []
        for role, dataset_id in (("fixed", fixed_id), ("feedback", feedback_id)):
            dataset = self.reviews.get_dataset(dataset_id)
            content = self.reviews.dataset_content(dataset, require_current=require_current)
            if content is None:
                current = False
                continue
            rows = [json.loads(line) for line in content.decode().splitlines() if line]
            groups.append({row["group_key"] for row in rows})
            bindings[role] = dict(dataset_id=str(dataset.id), sha256=dataset.manifest_json["samples_sha256"],
                                  sample_ids=[row["id"] for row in rows])
            samples.extend(dict(row, role=role) for row in rows)
        if current and groups[0] & groups[1]:
            raise ReviewConflict("固定集与反馈集有同组样本，请重新划分版本。")
        return samples, dict(bindings, fingerprint=digest(bindings)), current

    def create_job(self, payload):
        samples, inputs, _ = self.inputs(payload.fixed_dataset_id, payload.feedback_dataset_id)
        if len(samples) > payload.max_cases:
            raise ReviewConflict("两份版本的样本总数超过本次评测上限。")
        if self.session.scalar(select(Job.id).where(Job.tenant_id == self.principal.tenant_id, Job.active_slot == 1)):
            raise ReviewConflict("本租户已有排队或执行中的评测任务。")
        job = Job(tenant_id=self.principal.tenant_id, requested_by_user_id=self.principal.user_id,
            fixed_dataset_id=payload.fixed_dataset_id, feedback_dataset_id=payload.feedback_dataset_id,
            inputs_json=inputs, budget_json=payload.model_dump(exclude={"fixed_dataset_id", "feedback_dataset_id"}),
            runtime_json={}, records_json=[], summary_json={})
        try:
            with self.session.begin_nested():
                self.session.add(job); self.session.flush()
        except IntegrityError:
            raise ReviewConflict("本租户已有活动评测任务。") from None
        self.reviews.audit("quality.evaluation_queued", job.id)
        return job

    def get_job(self, job_id, *, require_current=False, lock=False):
        query = select(Job).where(Job.id == job_id, Job.tenant_id == self.principal.tenant_id)
        if lock: query = query.with_for_update().execution_options(populate_existing=True)
        job = self.session.scalar(query)
        if job is None: raise ObservationNotFound()
        _, inputs, current = self.inputs(job.fixed_dataset_id, job.feedback_dataset_id, require_current=require_current)
        current = current and inputs["fingerprint"] == job.inputs_json["fingerprint"]
        if require_current and not current: raise ReviewConflict("评测输入已变化，需要重新建立样本版本和评测任务。")
        items = [item for record in job.records_json for item in record.get("context", [])]
        citations = [item for record in job.records_json for item in record.get("response", {}).get("citations", [])]
        if not self.reviews.feedback_store.sources_available(SimpleNamespace(
                snapshot_json={"context": {"items": items}}, response_json={"citations": citations})):
            raise ObservationNotFound()
        return job, current

    def latest_reviews(self, job):
        result = {}
        rows = self.session.scalars(select(AnswerReview).where(AnswerReview.tenant_id == job.tenant_id,
            AnswerReview.job_id == job.id).order_by(AnswerReview.revision.desc()))
        for row in rows:
            if row.sample_id not in result:
                result[row.sample_id] = dict(revision=row.revision, verdict=row.verdict, reason=row.reason,
                    response_binding=row.response_binding, reviewer_user_id=str(row.reviewer_user_id), created_at=utc(row.created_at).isoformat())
        return result

    def job_data(self, job, current=True, *, detail=False):
        result = dict(id=job.id, status=job.status, current=current, requested_by_user_id=job.requested_by_user_id,
            fixed_dataset_id=job.fixed_dataset_id, feedback_dataset_id=job.feedback_dataset_id,
            created_at=utc(job.created_at), completed_cases=len(job.records_json),
            expected_cases=sum(len(job.inputs_json.get(role, {}).get("sample_ids", [])) for role in ("fixed", "feedback")),
            error_type=job.error_type, runtime=job.runtime_json, budget=job.budget_json, summary=job.summary_json)
        if detail:
            reviews = self.latest_reviews(job)
            result["records"] = [dict(row, human_review=reviews.get(row["sample"]["id"])) for row in job.records_json]
        return result

    def cancel(self, job_id):
        job, _ = self.get_job(job_id, lock=True)
        if job.status not in {"queued", "running"}: raise ReviewConflict("只有活动评测任务可以取消。")
        job.status, job.active_slot, job.lease_token, job.lease_expires_at = "cancelled", None, None, None
        job.finished_at = datetime.now(timezone.utc)
        self.reviews.audit("quality.evaluation_cancelled", job.id)
        return job

    def review_answer(self, job_id, sample_id, payload):
        job, _ = self.get_job(job_id, require_current=True, lock=True)
        if job.status != "succeeded": raise ReviewConflict("任务完整执行成功后才能提交答案复核。")
        record = next((row for row in job.records_json if row["sample"]["id"] == sample_id), None)
        if record is None: raise ObservationNotFound()
        if payload.response_binding != record["response_binding"]: raise ReviewConflict("复核所依据的答案或证据指纹已变化。")
        revision = self.session.scalar(select(func.max(AnswerReview.revision)).where(
            AnswerReview.tenant_id == job.tenant_id, AnswerReview.job_id == job.id, AnswerReview.sample_id == sample_id)) or 0
        self.session.add(AnswerReview(tenant_id=job.tenant_id, job_id=job.id, sample_id=sample_id,
            revision=revision + 1, response_binding=record["response_binding"], verdict=payload.verdict,
            reason=payload.reason, reviewer_user_id=self.principal.user_id))
        self.reviews.audit("quality.evaluation_reviewed", job.id)
        self.session.flush()
        return job

    def get_improvement(self, improvement_id, *, lock=False):
        query = select(Improvement).where(Improvement.id == improvement_id, Improvement.tenant_id == self.principal.tenant_id)
        if lock: query = query.with_for_update().execution_options(populate_existing=True)
        row = self.session.scalar(query)
        if row is None: raise ObservationNotFound()
        self.reviews.get_case(row.case_id)
        self.reviews.get_case(row.regression_case_id)
        # Old notes can refer to former regression samples or broader evaluation cohorts.
        # Reassignment must not make that history less restricted.
        dependencies = [row.verification_json] if row.verification_json else []
        events = self.session.scalars(select(ImprovementEvent).where(ImprovementEvent.tenant_id == row.tenant_id,
            ImprovementEvent.improvement_id == row.id))
        for event in events:
            historical_case = event.payload_json.get("regression_case_id")
            if historical_case: self.reviews.get_case(UUID(historical_case))
            if event.payload_json.get("verification"): dependencies.append(event.payload_json["verification"])
        checked_jobs = set()
        for decision in dependencies:
            for key in ("baseline_job_id", "candidate_job_id"):
                if decision[key] not in checked_jobs:
                    self.get_job(UUID(decision[key])); checked_jobs.add(decision[key])
        return row

    def improvement_data(self, row):
        return dict(id=row.id, case_id=row.case_id, regression_case_id=row.regression_case_id, revision=row.revision, status=row.status,
            owner_user_id=row.owner_user_id, description=row.description, change_ref=row.change_ref,
            policy=row.policy_json, verification=row.verification_json, updated_at=utc(row.updated_at))

    def event(self, row, note):
        self.session.add(ImprovementEvent(tenant_id=row.tenant_id, improvement_id=row.id,
            revision=row.revision, actor_user_id=self.principal.user_id,
            payload_json=dict(status=row.status, description=row.description, change_ref=row.change_ref, regression_case_id=str(row.regression_case_id),
                owner_user_id=str(row.owner_user_id), policy=row.policy_json, verification=row.verification_json, note=note)))
        self.reviews.audit("quality.improvement_updated", row.id)

    def save_improvement(self, payload, row=None):
        case, _, _ = self.reviews.get_case(payload.case_id)
        regression_id = payload.regression_case_id or case.id
        self.reviews.get_case(regression_id)
        if case.review_status != "confirmed": raise ReviewConflict("确认问题后才能建立改进记录。")
        owner_id = payload.owner_user_id or self.principal.user_id
        owner = self.session.scalar(select(User).where(User.id == owner_id, User.tenant_id == self.principal.tenant_id, User.status == "active"))
        if owner is None: raise ObservationNotFound()
        ReviewStore(self.session, Principal(owner.id, owner.tenant_id, owner.external_subject)).get_case(case.id)
        ReviewStore(self.session, Principal(owner.id, owner.tenant_id, owner.external_subject)).get_case(regression_id)
        if row is None:
            row = Improvement(tenant_id=self.principal.tenant_id, case_id=case.id)
            self.session.add(row)
        else:
            if row.revision != payload.expected_revision or row.case_id != payload.case_id:
                raise ReviewConflict("改进记录已变化或问题编号不匹配，请重新加载。")
            row.revision += 1
        row.description, row.change_ref, row.owner_user_id = payload.description, payload.change_ref, owner_id
        row.regression_case_id = regression_id
        row.policy_json = dict(max_latency_ratio=payload.max_latency_ratio, max_token_ratio=payload.max_token_ratio)
        row.status, row.verification_json, row.updated_at = "open", None, datetime.now(timezone.utc)
        self.session.flush(); self.event(row, "保存改进记录并等待验证")
        return row

    def compare(self, baseline_id, candidate_id, *, target_case_id=None, policy=None, acknowledge_unknown=False):
        baseline, _ = self.get_job(baseline_id, require_current=True)
        candidate, _ = self.get_job(candidate_id, require_current=True)
        if baseline.id == candidate.id: raise ReviewConflict("基线与候选必须是不同任务。")
        if baseline.status != "succeeded" or candidate.status != "succeeded": raise ReviewConflict("两个任务都须完整执行成功。")
        if baseline.inputs_json != candidate.inputs_json or baseline.runtime_json.get("values", {}).get("scorer") != candidate.runtime_json.get("values", {}).get("scorer"):
            raise ReviewConflict("样本、语料、范围或评分口径不同，不能比较。")
        policy = policy or dict(max_latency_ratio=1.5, max_token_ratio=1.3)
        failures, partitions = [], {}
        before = {row["sample"]["id"]: row for row in baseline.records_json}
        after = {row["sample"]["id"]: row for row in candidate.records_json}
        expected = set(baseline.inputs_json["fixed"]["sample_ids"] + baseline.inputs_json["feedback"]["sample_ids"])
        if set(before) != expected or set(after) != expected or len(before) != len(baseline.records_json) or len(after) != len(candidate.records_json):
            raise ReviewConflict("评测结果缺失或有重复样本。")
        for role in ("fixed", "feedback"):
            ids = baseline.inputs_json[role]["sample_ids"]
            regressions = [key for key in ids if before[key]["proxy_pass"] and not after[key]["proxy_pass"]]
            if regressions: failures.append(role + "_regression")
            if role == "feedback" and any(not after[key]["proxy_pass"] for key in ids): failures.append("feedback_not_fixed")
            partitions[role] = dict(total=len(ids), baseline_passed=sum(before[key]["proxy_pass"] for key in ids),
                candidate_passed=sum(after[key]["proxy_pass"] for key in ids), regressed_ids=regressions,
                improved_ids=[key for key in ids if not before[key]["proxy_pass"] and after[key]["proxy_pass"]])
        reviews = self.latest_reviews(candidate)
        unreviewed = [key for key in expected if reviews.get(key, {}).get("verdict") != "passed"
                      or reviews[key]["response_binding"] != after[key]["response_binding"]]
        if unreviewed: failures.append("human_review_incomplete")
        if any(not row["observations_complete"] for row in after.values()): failures.append("candidate_observation_incomplete")
        if target_case_id and "FB-" + str(target_case_id) not in candidate.inputs_json["feedback"]["sample_ids"]:
            failures.append("target_not_in_feedback_set")
        if candidate.runtime_json.get("fingerprint") != runtime_config(self.settings)["fingerprint"]: failures.append("candidate_runtime_changed")
        b, c = baseline.summary_json, candidate.summary_json
        for role in ("fixed", "feedback"):
            for metric in ("false_refusals", "wrong_accepts"):
                if c["partitions"][role][metric] > b["partitions"][role][metric]: failures.append(role + "_" + metric + "_growth")
        if c.get("p95_ms", 0) > b.get("p95_ms", 0) * policy["max_latency_ratio"]: failures.append("latency_growth")
        tokens_unknown = any(row["usage"]["unknown_usage_calls"] for row in (b, c))
        cost_unknown = any(row["usage"]["unpriced_calls"] for row in (b, c))
        usage_unknown = tokens_unknown or cost_unknown
        if usage_unknown and not acknowledge_unknown: failures.append("unknown_usage_requires_acknowledgement")
        if not tokens_unknown:
            if c["usage"]["reported_total_tokens"] > b["usage"]["reported_total_tokens"] * policy["max_token_ratio"]: failures.append("token_growth")
        if not cost_unknown:
            for currency, cost in c["usage"]["estimated_cost_by_currency"].items():
                old = Decimal(b["usage"]["estimated_cost_by_currency"].get(currency, "0"))
                if Decimal(cost) > old * Decimal(str(policy["max_token_ratio"])): failures.append("estimated_cost_growth:" + currency)
        bindings = dict(baseline_id=str(baseline.id), candidate_id=str(candidate.id), input_fingerprint=baseline.inputs_json["fingerprint"],
            target_case_id=str(target_case_id) if target_case_id else None,
            baseline_result=digest(baseline.records_json), candidate_result=digest(candidate.records_json),
            reviews=reviews, runtime=candidate.runtime_json, policy=policy, acknowledge_unknown=acknowledge_unknown)
        return dict(passed=not failures, failures=failures, partitions=partitions, unreviewed_ids=unreviewed,
            baseline_summary=b, candidate_summary=c, usage_unknown=bool(usage_unknown), binding=digest(bindings),
            limitation="自动事实/引用代理与人工复核；单次样本运行不代表生产泛化或容量。")

    def verify(self, row, payload):
        if row.status == "closed": raise ReviewConflict("关闭的问题须先更新改进记录重新开启。")
        if row.revision != payload.expected_revision: raise ReviewConflict("改进记录已变化，请重新加载。")
        comparison = self.compare(payload.baseline_job_id, payload.candidate_job_id,
            target_case_id=row.regression_case_id, policy=row.policy_json, acknowledge_unknown=payload.acknowledge_unknown_usage)
        if not comparison["passed"]: raise ReviewConflict("发布检查未通过：" + ", ".join(comparison["failures"]))
        row.revision += 1; row.status = "verified"
        row.verification_json = dict(baseline_job_id=str(payload.baseline_job_id), candidate_job_id=str(payload.candidate_job_id),
            binding=comparison["binding"], acknowledge_unknown_usage=payload.acknowledge_unknown_usage, note=payload.note)
        row.updated_at = datetime.now(timezone.utc); self.event(row, payload.note)
        return row

    def close(self, row, payload):
        if row.revision != payload.expected_revision or row.status != "verified": raise ReviewConflict("只有当前修订已验证的问题可以关闭。")
        verification = row.verification_json
        comparison = self.compare(UUID(verification["baseline_job_id"]), UUID(verification["candidate_job_id"]),
            target_case_id=row.regression_case_id, policy=row.policy_json, acknowledge_unknown=verification["acknowledge_unknown_usage"])
        if not comparison["passed"] or comparison["binding"] != verification["binding"]:
            raise ReviewConflict("验证依据已变化，请重新执行发布检查。")
        row.revision += 1; row.status = "closed"; row.updated_at = datetime.now(timezone.utc)
        self.event(row, payload.note)
        return row
