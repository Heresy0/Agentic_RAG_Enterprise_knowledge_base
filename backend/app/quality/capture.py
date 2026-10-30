"""Bounded request-local capture; no credentials, hidden reasoning or storage paths."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from hashlib import sha256
import json
from math import isfinite
from prometheus_client import Counter


_current = ContextVar("qa_snapshot", default=None)
MAX_CONTEXT_CHARACTERS = 100_000
MAX_CONTEXT_ITEMS = 100
MAX_DIAGNOSTIC_BYTES = 100_000
MAX_DIAGNOSTIC_EVENTS = 64
DIAGNOSTIC_STAGES = frozenset({'answer_generation', 'answer_regeneration', 'answer_schema_recovery',
    'answer_decisions', 'answer_completion', 'rag_routing'})
PIPELINE_STAGES = frozenset({"vector", "keyword", "fusion", "rerank_model", "rerank", "expanded_candidates",
    "policy_candidates_raw", "policy_candidates", "parent_context", "authorized_final", "initial_authorized", "agent_evidence", "feedback_evidence"})
QA_SNAPSHOTS = Counter("enterprise_qa_snapshots", "QA snapshot persistence outcomes", ["outcome"])


@dataclass
class SnapshotCapture:
    scope: object
    retrieval_question: str = ""
    history: list = field(default_factory=list)
    documents: dict = field(default_factory=dict)
    context: dict | None = None
    incomplete: bool = False
    pipeline: dict = field(default_factory=dict)
    pipeline_rows: int = 0
    pipeline_bytes: int = 0
    pipeline_limited: bool = False
    diagnostics: dict = field(default_factory=dict)
    diagnostic_bytes: int = 0
    diagnostic_events: int = 0
    diagnostics_limited: bool = False

    def observe_diagnostic(self, stage, payload):
        if stage not in DIAGNOSTIC_STAGES:
            return
        # Only structured, visible answer data and compiler events are accepted.
        # A diagnostic limit must not discard the usable evidence snapshot.
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        size = len(serialized.encode('utf-8'))
        if self.diagnostic_events >= MAX_DIAGNOSTIC_EVENTS or self.diagnostic_bytes + size > MAX_DIAGNOSTIC_BYTES:
            self.diagnostics_limited = True
            return
        self.diagnostics.setdefault(stage, []).append(json.loads(serialized))
        self.diagnostic_bytes += size
        self.diagnostic_events += 1

    def observe_stage(self, stage, documents):
        if stage not in PIPELINE_STAGES:
            return
        attempts = self.pipeline.setdefault(stage, [])
        if len(attempts) >= 3:
            self.pipeline_limited = True
            return
        rows = []
        limited = len(documents) > 40
        for rank, doc in enumerate(documents[:40], 1):
            meta = doc.metadata
            if not self.scope.contains(meta.get("tenant_id"), meta.get("knowledge_base_id")):
                self.incomplete = True
                continue
            row = dict(rank=rank, document_id=str(meta.get("document_id") or "")[:64],
                knowledge_base_id=str(meta.get("knowledge_base_id") or "")[:64],
                chunk_id=str(meta.get("chunk_id") or getattr(doc, "id", "") or "")[:128],
                parent_id=str(meta.get("parent_id") or "")[:64],
                vector_generation=str(meta.get("vector_generation") or "")[:64],
                content_sha256=sha256(doc.page_content.encode()).hexdigest())
            for key in ("document_version", "vector_distance", "bm25_score", "rrf_rank", "rrf_score", "rerank_score", "model_rank", "final_rank"):
                value = meta.get(key)
                if type(value) in (int, float) and isfinite(value):
                    row[key] = value
            size = len(json.dumps(row).encode())
            if self.pipeline_rows >= 500 or self.pipeline_bytes + size > 200_000:
                limited = True
                break
            rows.append(row); self.pipeline_rows += 1; self.pipeline_bytes += size
        attempts.append(dict(attempt=len(attempts)+1, items=rows, truncated=limited))
        self.pipeline_limited |= limited

    def observe_documents(self, documents):
        for doc in documents[:MAX_CONTEXT_ITEMS]:
            meta = doc.metadata
            if not self.scope.contains(meta.get("tenant_id"), meta.get("knowledge_base_id")):
                self.incomplete = True
                continue
            key = (str(meta.get("knowledge_base_id")), str(meta.get("document_id")))
            self.documents[key] = {key: meta.get(key) for key in ("document_version", "vector_generation")}

    def observe_context(self, context):
        items, used = [], 0
        self.incomplete = self.incomplete or bool(context.incomplete_reason) or len(context.items) > MAX_CONTEXT_ITEMS or len(context.text) > MAX_CONTEXT_CHARACTERS
        for item in context.items[:MAX_CONTEXT_ITEMS]:
            if str(item.knowledge_base_id) not in set(map(str, self.scope.knowledge_base_ids)):
                self.incomplete = True
                continue
            content = item.content
            if used + len(content) > MAX_CONTEXT_CHARACTERS:
                self.incomplete = True
                break
            used += len(content)
            items.append(dict(citation_id=item.citation_id, document_id=item.document_id,
                knowledge_base_id=str(item.knowledge_base_id), document_name=item.document_name,
                section_path=item.section_path, chunk_id=item.chunk_id, parent_id=item.parent_id,
                content=content, content_sha256=sha256(content.encode()).hexdigest(),
                **self.documents.get((str(item.knowledge_base_id), str(item.document_id)), {})))
        # If a scope violation occurred, do not persist the raw rendering either.
        self.context = dict(items=items, rendered_text=context.text if not self.incomplete else None,
                            incomplete_reason=context.incomplete_reason)

    def freeze(self):
        status = "incomplete" if self.incomplete else "captured" if self.context is not None else "not_observed"
        return status, dict(schema_version=2, context=self.context, history=self.history,
            generation=dict(stages=self.diagnostics if not self.incomplete else {},
                complete=not self.diagnostics_limited and not self.incomplete,
                limitation="仅保留可见首稿、覆盖记录和有限诊断事件；不记录原始提示词、凭据或隐藏推理。"),
            pipeline=dict(stages=self.pipeline, complete=not self.pipeline_limited,
                          limitation="仅记录有权限候选的ID、名次、分数和正文指纹；不记录候选正文、提示词或隐藏推理。"))


@contextmanager
def capture_snapshot(scope, question, *, enabled=True):
    capture = SnapshotCapture(scope=scope, retrieval_question=question) if enabled else None
    token = _current.set(capture)
    try:
        yield capture
    finally:
        _current.reset(token)


def observe_documents(documents):
    capture = _current.get()
    if capture is not None:
        capture.observe_documents(documents)


def observe_stage(stage, documents):
    capture = _current.get()
    if capture is not None:
        capture.observe_stage(stage, documents)


def observe_context(context):
    capture = _current.get()
    if capture is not None:
        capture.observe_context(context)


def observe_diagnostic(stage, payload):
    capture = _current.get()
    if capture is not None:
        capture.observe_diagnostic(stage, payload)


def observe_history(turns):
    capture = _current.get()
    if capture is not None:
        capture.history = [dict(question=t.question, retrieval_question=t.retrieval_question,
                                answer=str(t.result_json.get("answer", ""))[:1200],
                                answerable=t.result_json.get("answerable", False),
                                source_document_ids=sorted({str(c["document_id"]) for c in t.result_json.get("citations", []) if c.get("document_id")})) for t in turns[-6:]]


def observe_resolution(question):
    capture = _current.get()
    if capture is not None:
        capture.retrieval_question = question
