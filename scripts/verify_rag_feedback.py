"""Opt-in full synthetic AnswerService feedback smoke with real generation calls.

Retrieval/identity/freshness are synthetic; never label this as HTTP quality eval.
"""
import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.documents import Document
from backend.app.config import Settings
from backend.app.evaluation.trace import capture_trace, record_documents, record_scope
from backend.app.knowledge.answer_service import AnswerService
from backend.app.knowledge.evidence_feedback import EvidenceFeedback
from backend.app.knowledge.evidence_selection import select_evidence
from backend.app.knowledge.answer_coverage import coverage_errors
from pydantic import ValidationError
from backend.app.model_usage import summarize_usage, usage_context
from backend.app.security.retrieval_scope import RetrievalScope


class SyntheticGuard:
    available = True
    def require(self):
        pass


class SyntheticRetrieval:
    def __init__(self, scope):
        self.scope, self.searches = scope, []
        self.docs = [Document(id=name, page_content=f"{name}工艺的温度为{temperature}摄氏度。",
            metadata=dict(tenant_id=str(scope.tenant_id), knowledge_base_id=str(scope.knowledge_base_id),
                chunk_id=name, vector_generation="a"*32, document_id=str(uuid4()), document_name=f"合成{name}工艺说明",
                section_path="温度", source=f"synthetic/{name}.txt")) for name, temperature in [("甲", 80), ("乙", 90)]]

    def search(self, query, *, scope, session):
        assert scope == self.scope
        self.searches.append(query)
        record_scope(query, scope)
        docs = self.docs[:1] if len(self.searches) == 1 else self.docs[1:]
        record_documents("rerank", docs)
        return select_evidence(query, docs)

    def refresh_evidence(self, docs, *, scope, session):
        assert scope == self.scope
        return docs


class ObservedAnswerService(AnswerService):
    """One isolated synthetic request; store visible initial draft for diagnostics."""
    def _generate_draft(self, *, question, context):
        result = super()._generate_draft(question=question, context=context)
        self.observed_gap = result.evidence_gap.model_dump() if result.evidence_gap else None
        self.observed_coverage_errors = coverage_errors(result, question, context)
        self.observed_refusal = result.refusal_reason
        self.observed_coverage = [row.model_dump() for row in result.coverage]
        self.observed_claims_count = len(result.claims)
        return result


def main():
    parser = argparse.ArgumentParser(description="使用合成正文验证真实模型的缺证据反馈和一次再生成，产生模型费用。")
    parser.add_argument("--allow-model-calls", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("data/ingestion-reports/rag-feedback/model-smoke.json"))
    args = parser.parse_args()
    if not args.allow_model_calls:
        parser.error("需要 --allow-model-calls")
    if args.output.exists():
        parser.error("报告已存在，请使用新路径")
    settings, scope = Settings(), RetrievalScope(uuid4(), uuid4())
    settings.rag_planner_mode, settings.agentic_rag_mode = "off", "off"
    settings.rag_repair_enabled, settings.rag_feedback_enabled = False, True
    settings.rag_max_optional_calls = 1  # Only the single regeneration may be extra.
    retrieval = SyntheticRetrieval(scope)
    service = ObservedAnswerService(settings=settings, retrieval_service=retrieval)
    service._evidence_feedback = EvidenceFeedback(settings, retrieval, service._evidence_coordinator,
        service._context_builder, guard_factory=lambda *args: SyntheticGuard())
    with usage_context(scope=scope, settings=settings) as usage, capture_trace() as trace:
        try:
            result = service.answer("甲工艺与乙工艺的温度分别是多少？", scope=scope, session=None)
            budgets = [row for row in trace.stages.get("rag_routing", []) if row.get("kind") == "budget"]
            first = trace.stages.get("answer_generation", [{}])[0]
            passed = (result.answerable and "80" in result.answer and "90" in result.answer
                and first.get("response", {}).get("answerable") is False and len(retrieval.searches) == 2
                and budgets and budgets[0]["feedback_attempts"] == budgets[0]["regenerations"] == 1
                and [row["stage"] for row in usage.events] == ["answer_generation", "answer_regeneration"])
            outcome = dict(status="passed" if passed else "failed", answerable=result.answerable,
                searches=len(retrieval.searches), budget=budgets[0] if budgets else {},
                feedback=[row for row in trace.stages.get("rag_routing", []) if row.get("kind") == "feedback"])
            outcome['initial_gap'] = service.observed_gap
            outcome['initial_coverage_errors'] = service.observed_coverage_errors
            outcome['initial_refusal'] = service.observed_refusal
            outcome['initial_coverage'] = service.observed_coverage
            outcome['initial_claims_count'] = service.observed_claims_count
        except Exception as exc:
            outcome = dict(status="failed", error_type=type(exc).__name__)
            if isinstance(exc, ValidationError):
                outcome['field_errors'] = [dict(location=list(row['loc']), type=row['type'])
                    for row in exc.errors(include_input=False, include_context=False, include_url=False)]
    report = dict(synthetic_feedback_smoke=outcome, model_usage=summarize_usage(usage.events),
        limitation="Real initial generation and regeneration on synthetic evidence; retrieval, actor and SQL freshness are simulated. Not authenticated HTTP or full corpus evaluation. Standalone usage not persisted to SQL.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if outcome["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
