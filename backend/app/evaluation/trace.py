"""Request-local, opt-in pipeline snapshots. No global logs or API debug fields."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from prometheus_client import Counter

RAG_ROUTES = Counter("enterprise_rag_routes", "RAG strategy selections by phase", ["phase", "strategy"])
RAG_REPAIRS = Counter("enterprise_rag_repairs", "Bounded answer repair outcomes", ["outcome"])
RAG_FEEDBACK = Counter("enterprise_rag_feedback", "One evidence feedback round outcomes", ["outcome"])


@dataclass
class EvaluationTrace:
    stages: dict = field(default_factory=dict)
    scope: dict = field(default_factory=dict)
    query: str = ""

    def to_dict(self):
        return deepcopy(dict(query=self.query, scope=self.scope, stages=self.stages))


_current = ContextVar("evaluation_trace", default=None)


@contextmanager
def capture_trace():
    trace = EvaluationTrace()
    token = _current.set(trace)
    try:
        yield trace
    finally:
        _current.reset(token)


def record_model_usage(row):
    trace = _current.get()
    if trace is not None:
        trace.stages.setdefault("model_usage", []).append(deepcopy(row))


def start_model_usage():
    trace = _current.get()
    if trace is not None:
        trace.stages.setdefault("model_usage", [])


def record_scope(query, scope):
    trace = _current.get()
    if trace is not None:
        trace.query = query
        trace.scope = dict(tenant_id=str(scope.tenant_id),
                           knowledge_base_ids=sorted(map(str, scope.knowledge_base_ids)))


def _in_scope(trace, metadata):
    return (str(metadata.get("tenant_id")) == trace.scope.get("tenant_id")
            and str(metadata.get("knowledge_base_id")) in trace.scope.get("knowledge_base_ids", []))


def record_documents(stage, documents):
    trace = _current.get()
    if trace is None:
        return
    rows = []
    for rank, document in enumerate(documents, 1):
        metadata = document.metadata
        # A faulty retriever must not cause a diagnostic file to leak other scopes.
        if not _in_scope(trace, metadata):
            rows.append(dict(rank=rank, scope_violation=True, content="", document_name=""))
            continue
        name = metadata.get("document_name") or PurePosixPath(str(metadata.get("source", "")).replace("\\", "/")).name
        rows.append(dict(rank=rank, document_name=str(name), content=document.page_content,
                         chunk_id=str(getattr(document, "id", None) or metadata.get("chunk_id") or ""),
                         document_id=str(metadata.get("document_id") or ""),
                         tenant_id=str(metadata.get("tenant_id")),
                         knowledge_base_id=str(metadata.get("knowledge_base_id")),
                         rerank_status=metadata.get("rerank_status"),
                         policy=metadata.get("policy"),
                         policy_selection=metadata.get("policy_selection"),
                         matched_incident_ids=metadata.get('matched_incident_ids', []),
                         evidence_role=metadata.get("evidence_role"),
                         quality=metadata.get("quality"),
                         vector_generation=metadata.get("vector_generation"),
                         cleaning_version=metadata.get("cleaning_version"),
                         chunk_type=metadata.get("chunk_type"),
                         parser=metadata.get("parser"),
                         section_path=metadata.get("section_path")))
    trace.stages[stage] = rows


def record_context(context, *, stage="context"):
    trace = _current.get()
    if trace is None:
        return
    rows = []
    for item in context.items:
        if str(item.knowledge_base_id) not in trace.scope.get("knowledge_base_ids", []):
            rows.append(dict(scope_violation=True, content="", document_name=""))
        else:
            rows.append(dict(document_name=item.document_name, content=item.content,
                             chunk_id=item.chunk_id, citation_id=item.citation_id,
                             policy=item.policy,
                             evidence_role=item.evidence_role,
                             quality=item.quality,
                             parser=item.parser,
                             cleaning_version=item.cleaning_version,
                             matched_incident_ids=list(item.matched_incident_ids),
                             knowledge_base_id=str(item.knowledge_base_id)))
    trace.stages[stage] = rows


def record_calculations(records):
    trace = _current.get()
    if trace is not None:
        trace.stages["calculations"] = deepcopy(records)


def record_context_selection(decision):
    trace = _current.get()
    if trace is not None:
        trace.stages["context_selection"] = [deepcopy(decision)]


def record_answer_decision(*, stage, answerable, reason=None, temporal=None):
    """Structured outcome only; never collect hidden model reasoning."""
    trace = _current.get()
    if trace is not None:
        trace.stages.setdefault("answer_decisions", []).append(dict(
            stage=stage, answerable=answerable, reason=reason, temporal=deepcopy(temporal),
        ))


def record_answer_completion(records):
    trace = _current.get()
    if trace is not None:
        trace.stages['answer_completion'] = deepcopy(records)


def record_answer_generation(response, claims, coverage, *, changed, validation_errors, stage="answer_generation"):
    """Opt-in visible first answer/coverage only, never hidden reasoning or public API fields."""
    trace = _current.get()
    if trace is not None:
        trace.stages[stage] = [dict(
            response=response.model_dump(mode='json'), claims=deepcopy(claims),
            coverage=deepcopy(coverage), completion_changed=changed,
            validation_errors=list(validation_errors),
        )]


def record_conversation_resolution(question, resolved, history_count):
    trace = _current.get()
    if trace is not None:
        trace.stages['conversation_resolution'] = [dict(
            question=question, retrieval_question=resolved, history_count=history_count,
        )]


def record_agent_event(event):
    """Visible actions/limits only; no hidden reasoning or credentials."""
    trace = _current.get()
    if trace is not None:
        trace.stages.setdefault("agent_decisions", []).append(deepcopy(event))


def record_rag_event(event):
    """Visible plans, strategy transitions and repair outcomes, opt-in only."""
    kind = event.get("kind")
    if kind in {"plan", "retrieval_route", "strategy_transition"}:
        strategy = event.get("selected_strategy", event.get("strategy"))
        if strategy in {"standard", "reflective", "agentic"}:
            RAG_ROUTES.labels(kind, strategy).inc()
    if kind == "repair":
        RAG_REPAIRS.labels(event["outcome"]).inc()
    if kind == "feedback":
        RAG_FEEDBACK.labels(event["outcome"]).inc()
    trace = _current.get()
    if trace is not None:
        trace.stages.setdefault("rag_routing", []).append(deepcopy(event))


def record_agent_retrieval(snapshot):
    trace = _current.get()
    if trace is not None:
        trace.stages.setdefault("agent_retrieval", []).append(deepcopy(snapshot))
        for row in snapshot.get("stages", {}).get("model_usage", []):
            record_model_usage(row)


def record_feedback_retrieval(snapshot):
    trace = _current.get()
    if trace is not None:
        trace.stages.setdefault("feedback_retrieval", []).append(deepcopy(snapshot))
        for row in snapshot.get("stages", {}).get("model_usage", []):
            record_model_usage(row)
