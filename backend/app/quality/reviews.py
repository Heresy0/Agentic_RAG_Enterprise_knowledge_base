"""Human-reviewed cases and tenant-private, versioned regression exports."""
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import select

from ..audit_service import AuditService
from ..db.models import KnowledgeDocument
from ..db.quality_models import QARun, QARunScope, QAFeedback
from ..db.review_models import QualityCase, QualityCaseRevision, QualityDatasetVersion
from .service import FeedbackStore, ObservationNotFound


class ReviewConflict(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def feedback_snapshot(row):
    return dict(id=str(row.id), rating=row.rating, reason=row.reason, comment=row.comment,
                share_with_admins=row.share_with_admins, allow_dataset_export=row.allow_dataset_export,
                updated_at=utc(row.updated_at).isoformat())


def source_binding(run, feedback):
    return digest(dict(run_id=str(run.id), question=run.question, retrieval_question=run.retrieval_question,
        response=run.response_json, snapshot=run.snapshot_json, config=run.config_json,
        feedback=feedback_snapshot(feedback)))


class ReviewStore:
    def __init__(self, session, principal):
        self.session, self.principal = session, principal
        self.feedback_store = FeedbackStore(session, principal)

    def require_run(self, run_id, *, lock=False):
        statement = select(QARun).where(QARun.id == run_id,
            *self.feedback_store.scope_predicates(self.feedback_store.allowed_ids(admin=True)))
        if lock:
            statement = statement.with_for_update()
        run = self.session.scalar(statement)
        if run is None or not self.feedback_store.sources_available(run):
            raise ObservationNotFound()
        feedback = self.session.scalar(select(QAFeedback).where(QAFeedback.tenant_id == run.tenant_id,
            QAFeedback.run_id == run.id, QAFeedback.user_id == run.user_id).execution_options(populate_existing=True))
        if feedback is None or not feedback.share_with_admins:
            raise ObservationNotFound()
        return run, feedback

    def scopes(self, run):
        return sorted(str(value) for value in self.session.scalars(select(QARunScope.knowledge_base_id).where(
            QARunScope.tenant_id == run.tenant_id, QARunScope.run_id == run.id)))

    def corpus_binding(self, run):
        ids = [UUID(value) for value in self.scopes(run)]
        rows = self.session.execute(select(KnowledgeDocument.id, KnowledgeDocument.version,
            KnowledgeDocument.content_hash, KnowledgeDocument.status, KnowledgeDocument.metadata_json).where(
                KnowledgeDocument.tenant_id == run.tenant_id, KnowledgeDocument.knowledge_base_id.in_(ids))
            .order_by(KnowledgeDocument.id)).all()
        return digest([[str(row.id), row.version, row.content_hash, row.status] +
            ([row.metadata_json["publication"]] if (row.metadata_json or {}).get("publication") is not None else []) for row in rows])

    def audit(self, action, resource_id):
        AuditService(self.session).add(tenant_id=self.principal.tenant_id,
            actor_user_id=self.principal.user_id, action=action, resource_type="quality",
            resource_id=resource_id, outcome="success")

    def revision(self, case):
        return self.session.get(QualityCaseRevision, (case.tenant_id, case.id, case.revision))

    def get_case(self, case_id, *, lock=False):
        case = self.session.scalar(select(QualityCase).where(
            QualityCase.id == case_id, QualityCase.tenant_id == self.principal.tenant_id))
        if case is None:
            raise ObservationNotFound()
        run, feedback = self.require_run(case.run_id, lock=lock)
        if lock:
            case = self.session.scalar(select(QualityCase).where(QualityCase.id == case.id,
                QualityCase.tenant_id == self.principal.tenant_id).with_for_update().execution_options(populate_existing=True))
        return case, run, feedback

    def open_case(self, run_id):
        run, feedback = self.require_run(run_id, lock=True)
        case = self.session.scalar(select(QualityCase).where(QualityCase.tenant_id == run.tenant_id,
                                                              QualityCase.run_id == run.id))
        if case is not None:
            return case, run, feedback, False
        case = QualityCase(tenant_id=run.tenant_id, run_id=run.id, source_binding=source_binding(run, feedback))
        self.session.add(case); self.session.flush()
        payload = dict(review_status="pending", sample_status="draft", root_cause=None, review_reason="新建审核",
                       expected_answerable=None, reference_answer=None, required_facts=[], forbidden_claims=[], evidence=[],
                       evaluation_question=run.retrieval_question,
                       feedback_snapshot=feedback_snapshot(feedback))
        self.session.add(QualityCaseRevision(tenant_id=run.tenant_id, case_id=case.id, revision=case.revision,
            reviewer_user_id=self.principal.user_id, payload_json=payload, source_binding=case.source_binding))
        self.audit("quality.case_opened", case.id)
        self.session.flush()
        return case, run, feedback, True

    def resolve_evidence(self, run, selected, *, require_current=True):
        context = run.snapshot_json.get("context") or {}
        by_id = {item["citation_id"]: item for item in context.get("items", [])}
        result = []
        for chosen in selected:
            item = by_id.get(chosen.citation_id)
            if item is None or chosen.quote not in item["content"]:
                raise ReviewConflict("证据摘录必须是所选快照片段中的连续原文。")
            try:
                document_id = UUID(str(item.get("document_id")))
            except (TypeError, ValueError):
                raise ReviewConflict("快照缺少可验证的来源文档编号。") from None
            document = self.session.scalar(select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id, KnowledgeDocument.tenant_id == run.tenant_id))
            if document is None or require_current and (document.status != "ready" or document.version != item.get("document_version")):
                raise ReviewConflict("证据来源版本已变化或尚未就绪，请补充新快照后审核。")
            result.append(dict(citation_id=chosen.citation_id, quote=chosen.quote,
                document=item["document_name"], document_id=item["document_id"], knowledge_base_id=item["knowledge_base_id"],
                document_version=item.get("document_version"), vector_generation=item.get("vector_generation"),
                chunk_id=item.get("chunk_id"), parent_id=item.get("parent_id"),
                quote_sha256=hashlib.sha256(chosen.quote.encode()).hexdigest(), context_sha256=item["content_sha256"]))
        return result

    def update_case(self, case_id, payload):
        case, run, feedback = self.get_case(case_id, lock=True)
        if payload.expected_revision != case.revision:
            raise ReviewConflict("审核记录已被其他人更新，请重新加载后再提交。")
        if payload.sample_status == "approved" and run.snapshot_status != "captured":
            raise ReviewConflict("快照不完整或未采集，不能批准回归样本。")
        evidence = self.resolve_evidence(run, payload.evidence, require_current=payload.sample_status == "approved")
        values = payload.model_dump(exclude={"expected_revision", "evidence"})
        values.update(evidence=evidence, feedback_snapshot=feedback_snapshot(feedback), corpus_sha256=self.corpus_binding(run))
        case.revision += 1
        case.review_status, case.sample_status, case.root_cause = payload.review_status, payload.sample_status, payload.root_cause
        case.source_binding = source_binding(run, feedback)
        case.updated_at = datetime.now(timezone.utc)
        self.session.add(QualityCaseRevision(tenant_id=case.tenant_id, case_id=case.id, revision=case.revision,
            reviewer_user_id=self.principal.user_id, payload_json=values, source_binding=case.source_binding))
        self.audit("quality.case_reviewed", case.id)
        self.session.flush()
        return case, run, feedback

    def eligibility(self, case, run, feedback):
        if case is None or case.sample_status != "approved" or case.review_status != "confirmed":
            return False, "not_approved"
        if case.source_binding != source_binding(run, feedback):
            return False, "feedback_changed"
        if not feedback.allow_dataset_export:
            return False, "export_not_authorized"
        revision = self.revision(case)
        if revision is None or revision.payload_json.get("corpus_sha256") != self.corpus_binding(run):
            return False, "corpus_changed"
        return True, None

    def case_data(self, case, run, feedback):
        eligible, reason = self.eligibility(case, run, feedback)
        return dict(id=case.id, run_id=run.id, revision=case.revision, review_status=case.review_status,
            sample_status=case.sample_status, root_cause=case.root_cause,
            source_changed=case.source_binding != source_binding(run, feedback),
            export_eligible=eligible, export_blocked_reason=reason, updated_at=utc(case.updated_at))

    def sample(self, case, run):
        payload = self.revision(case).payload_json
        group = str(run.conversation_id) if run.conversation_id else " ".join(payload["evaluation_question"].split())
        return dict(id="FB-" + str(case.id), question=payload["evaluation_question"], original_question=run.question,
            answerable=payload["expected_answerable"], reference_answer=payload["reference_answer"],
            **({"expected_behavior": payload["expected_behavior"]} if payload.get("expected_behavior") else {}),
            required_facts=payload["required_facts"], forbidden_claims=payload["forbidden_claims"],
            **{key: payload[key] for key in ('fact_alternatives', 'fact_assertions', 'accepted_behaviors', 'citation_requirement')
               if payload.get(key)},
            expected_evidence=[dict(item, text=item["quote"]) for item in payload["evidence"]],
            category=case.root_cause, split="regression", source_groups=self.scopes(run),
            group_key=digest([str(run.tenant_id), group]), annotation_version="feedback-review-v1",
            provenance=dict(run_id=str(run.id), request_id=run.request_id, case_revision=case.revision,
                observed_retrieval_question=run.retrieval_question,
                reviewer_user_id=str(self.revision(case).reviewer_user_id), source_binding=case.source_binding,
                corpus_sha256=payload["corpus_sha256"]))

    def create_dataset(self, payload):
        samples, members = [], []
        for case_id in sorted(payload.case_ids):
            case, run, feedback = self.get_case(case_id, lock=True)
            eligible, reason = self.eligibility(case, run, feedback)
            if not eligible:
                raise ReviewConflict("所选样本尚不能导出：" + reason)
            samples.append(self.sample(case, run))
            members.append(dict(case_id=str(case.id), revision=case.revision, source_binding=case.source_binding))
        content = "".join(canonical(sample) + "\n" for sample in samples).encode()
        dataset = QualityDatasetVersion(id=uuid4(), tenant_id=self.principal.tenant_id,
            name=payload.name, created_by_user_id=self.principal.user_id,
            manifest_json=dict(format_version=1, annotation_version="feedback-review-v1", split="regression",
                members=members, sample_count=len(samples), samples_sha256=hashlib.sha256(content).hexdigest()))
        self.session.add(dataset); self.audit("quality.dataset_created", dataset.id); self.session.flush()
        return dataset

    def dataset_content(self, dataset, *, require_current=True):
        samples, stale = [], False
        for member in dataset.manifest_json["members"]:
            case, run, feedback = self.get_case(UUID(member["case_id"]), lock=require_current)
            # Export consent is checked again even for previously created versions.
            if not feedback.allow_dataset_export:
                raise ObservationNotFound()
            eligible, _ = self.eligibility(case, run, feedback)
            current = eligible and case.revision == member["revision"] and case.source_binding == member["source_binding"]
            stale = stale or not current
            if current:
                samples.append(self.sample(case, run))
        if stale:
            if require_current:
                raise ReviewConflict("样本审核、反馈或语料已变化，请重新审核并创建新版本。")
            return None
        content = "".join(canonical(sample) + "\n" for sample in samples).encode()
        if hashlib.sha256(content).hexdigest() != dataset.manifest_json["samples_sha256"]:
            raise ReviewConflict("版本内容指纹不一致，停止导出。")
        return content

    def get_dataset(self, dataset_id):
        dataset = self.session.scalar(select(QualityDatasetVersion).where(
            QualityDatasetVersion.id == dataset_id, QualityDatasetVersion.tenant_id == self.principal.tenant_id))
        if dataset is None:
            raise ObservationNotFound()
        return dataset
