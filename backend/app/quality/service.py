"""Persist observations and enforce present-day access to historical data."""
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import exists, select

from ..db.conversation_models import ConversationTurn
from ..db.models import KnowledgeDocument
from ..db.quality_models import QARun, QARunScope, QAFeedback
from ..security.authorization import AuthorizationService


class ObservationNotFound(LookupError):
    pass


@lru_cache(maxsize=1)
def pipeline_fingerprint():
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return dict(code_sha256=digest.hexdigest(), prompts_sha256=hashlib.sha256((root / "knowledge/prompts.py").read_bytes()).hexdigest())


def save_run(session, *, run_id, request_id, principal, scope, question, result, capture, settings, conversation_id=None, model_usage=()):
    status, snapshot = capture.freeze()
    config = {name: getattr(settings, name, None) for name in (
        "chat_model", "embedding_model", "rerank_model", "agentic_rag_mode", "rag_planner_mode",
        "rag_feedback_enabled", "rag_repair_enabled", "rag_max_searches", "rag_rerank_top_k",
        "rag_parent_context_enabled", "rag_context_selection_enabled")}
    config["pipeline"] = pipeline_fingerprint()
    config["sha256"] = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    snapshot["model_usage"] = [{key: row.get(key) for key in (
        "stage", "provider", "model", "status", "input_tokens", "output_tokens", "total_tokens",
        "estimated_cost", "currency", "persisted")} for row in model_usage]
    if len(json.dumps(snapshot, ensure_ascii=False).encode()) > 2_000_000:
        status, snapshot = "incomplete", dict(schema_version=1, context=None, history=[])
    # A failed diagnostic write rolls back only its savepoint, not the completed conversation.
    with session.begin_nested():
        turn = None
        if conversation_id is not None:
            turn = session.scalar(select(ConversationTurn).where(
                ConversationTurn.tenant_id == scope.tenant_id,
                ConversationTurn.conversation_id == conversation_id,
            ).order_by(ConversationTurn.turn_index.desc()).limit(1))
        row = QARun(id=run_id, request_id=request_id, tenant_id=scope.tenant_id,
            user_id=principal.user_id, conversation_id=conversation_id,
            turn_index=turn.turn_index if turn else None, question=question.strip(),
            retrieval_question=capture.retrieval_question, response_json=result.model_dump(mode="json"),
            snapshot_json=snapshot, snapshot_status=status, config_json=config)
        session.add(row)
        session.flush()
        session.add_all([QARunScope(tenant_id=scope.tenant_id, run_id=run_id, knowledge_base_id=kb)
                         for kb in scope.knowledge_base_ids])
        session.flush()
    return row


class FeedbackStore:
    def __init__(self, session, principal):
        self.session, self.principal = session, principal
        self.authorization = AuthorizationService(session)

    def allowed_ids(self, *, admin=False):
        ids = self.authorization.list_readable_knowledge_base_ids(principal=self.principal)
        return {kb for kb in ids if not admin or self.authorization.get_permission(
            principal=self.principal, knowledge_base_id=kb) == "admin"}

    def scope_predicates(self, allowed):
        scoped = QARunScope
        same_run = (scoped.tenant_id == QARun.tenant_id, scoped.run_id == QARun.id)
        return [QARun.tenant_id == self.principal.tenant_id,
                exists(select(scoped.run_id).where(*same_run)),
                ~exists(select(scoped.run_id).where(*same_run, scoped.knowledge_base_id.not_in(allowed)))]

    def sources_available(self, run):
        context = run.snapshot_json.get("context") or {}
        values = [item.get("document_id") for item in context.get("items", [])]
        values += [item.get("document_id") for item in run.response_json.get("citations", [])]
        values += [value for turn in run.snapshot_json.get("history", []) for value in turn.get("source_document_ids", [])]
        try:
            ids = {UUID(str(value)) for value in values if value}
        except ValueError:
            return False
        if not ids:
            return True
        available = set(self.session.scalars(select(KnowledgeDocument.id).where(
            KnowledgeDocument.tenant_id == self.principal.tenant_id,
            KnowledgeDocument.id.in_(ids), KnowledgeDocument.status != "deleted")))
        return available == ids

    def get_run(self, run_id, *, owner=False, lock=False):
        statement = select(QARun).where(QARun.id == run_id, *self.scope_predicates(self.allowed_ids()))
        if owner:
            statement = statement.where(QARun.user_id == self.principal.user_id)
        if lock:
            statement = statement.with_for_update()
        run = self.session.scalar(statement)
        if run is None or not self.sources_available(run):
            raise ObservationNotFound()
        if run.user_id != self.principal.user_id:
            feedback = self.session.scalar(select(QAFeedback).where(
                QAFeedback.tenant_id == self.principal.tenant_id, QAFeedback.run_id == run.id,
                QAFeedback.share_with_admins.is_(True)))
            required = set(self.session.scalars(select(QARunScope.knowledge_base_id).where(
                QARunScope.tenant_id == run.tenant_id, QARunScope.run_id == run.id)))
            if feedback is None or not required <= self.allowed_ids(admin=True):
                raise ObservationNotFound()
        return run

    def feedback(self, run):
        return self.session.scalar(select(QAFeedback).where(
            QAFeedback.tenant_id == run.tenant_id, QAFeedback.run_id == run.id,
            QAFeedback.user_id == run.user_id))

    def put(self, run, payload):
        row = self.feedback(run)
        if row is None:
            row = QAFeedback(tenant_id=run.tenant_id, run_id=run.id, user_id=self.principal.user_id)
            self.session.add(row)
        for name, value in payload.model_dump().items():
            setattr(row, name, value)
        row.updated_at = datetime.now(timezone.utc)
        self.session.flush()
        return row
