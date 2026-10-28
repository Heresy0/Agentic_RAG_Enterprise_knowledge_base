"""Bounded request-local capture; no credentials, hidden reasoning or storage paths."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from hashlib import sha256
from prometheus_client import Counter


_current = ContextVar("qa_snapshot", default=None)
MAX_CONTEXT_CHARACTERS = 100_000
MAX_CONTEXT_ITEMS = 100
QA_SNAPSHOTS = Counter("enterprise_qa_snapshots", "QA snapshot persistence outcomes", ["outcome"])


@dataclass
class SnapshotCapture:
    scope: object
    retrieval_question: str = ""
    history: list = field(default_factory=list)
    documents: dict = field(default_factory=dict)
    context: dict | None = None
    incomplete: bool = False

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
        return status, dict(schema_version=1, context=self.context, history=self.history)


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


def observe_context(context):
    capture = _current.get()
    if capture is not None:
        capture.observe_context(context)


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
