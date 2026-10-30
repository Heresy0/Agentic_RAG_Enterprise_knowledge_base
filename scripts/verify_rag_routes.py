"""Opt-in synthetic planner/repair interface smoke; not an HTTP quality eval."""
import argparse
import json
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.documents import Document

from backend.app.config import Settings
from backend.app.evaluation.trace import capture_trace
from backend.app.knowledge.answer_models import GenerationAnswerDraft
from backend.app.knowledge.answer_repair import AnswerRepair
from backend.app.knowledge.answer_validation import AnswerValidator
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.knowledge.query_planner import QueryPlanner
from backend.app.knowledge.rag_budget import RagBudget, rag_budget_scope
from backend.app.model_usage import summarize_usage, usage_context
from backend.app.security.retrieval_scope import RetrievalScope


class SyntheticGuard:
    """No business identity/database in this interface-only smoke."""
    available = True
    def require(self):
        pass


def main():
    parser = argparse.ArgumentParser(description="使用合成数据验证输入路由和一次覆盖修复，会调用已配置模型并产生费用。")
    parser.add_argument("--allow-model-calls", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("data/ingestion-reports/rag-routing/model-smoke.json"))
    args = parser.parse_args()
    if not args.allow_model_calls:
        parser.error("需要 --allow-model-calls")
    if args.output.exists():
        parser.error("报告已存在，请使用新的 --output")
    settings, scope = Settings(), RetrievalScope(uuid4(), uuid4())
    settings.rag_planner_mode, settings.rag_repair_enabled = "auto", True
    guard_factory = lambda *args: SyntheticGuard()
    planner = QueryPlanner(settings, guard_factory=guard_factory)
    results = []
    with usage_context(scope=scope, settings=settings) as usage:
        for question, expected in [("甲工艺温度是多少？", "standard"),
                                   ("甲工艺上线需要哪些条件？", "reflective"),
                                   ("比较甲工艺和乙工艺的温度，并解释差异", "agentic")]:
            started, cursor = perf_counter(), len(usage.events)
            with rag_budget_scope(RagBudget.from_settings(settings)), capture_trace() as trace:
                try:
                    plan = planner.plan(question, scope=scope, session=None)
                    source = trace.stages["rag_routing"][0]["source"]
                    # A comparison may validly route reflective if one lookup suffices.
                    passed = (plan.strategy == expected if expected != "agentic" else
                              source == "model" and plan.strategy in {"agentic", "reflective"}
                              and 2 <= len(plan.subquestions) <= 3)
                    results.append(dict(kind="planning", expected=expected, strategy=plan.strategy, source=source,
                        subquestions=len(plan.subquestions), status="passed" if passed else "failed",
                        physical_calls=len(usage.events) - cursor, latency_ms=round((perf_counter()-started)*1000, 2)))
                except Exception as exc:
                    results.append(dict(kind="planning", status="failed", error_type=type(exc).__name__))
        doc = Document(page_content="甲工艺的温度为80摄氏度。", metadata=dict(tenant_id=str(scope.tenant_id),
            knowledge_base_id=str(scope.knowledge_base_id), chunk_id="synthetic-temperature",
            document_name="合成工艺说明", section_path="温度"))
        context = ContextBuilder().build([doc], question="甲工艺温度是多少？")
        draft = GenerationAnswerDraft.model_validate(dict(answerable=True, response_type='answer', claims=[
            dict(text="甲工艺的温度为80摄氏度", citations=["资料1"])], refusal_reason=None,
            coverage=[dict(requirement_id="Q1", aspect="工艺温度", status="answered", claim_indices=[2], missing_information=None)]))
        with rag_budget_scope(RagBudget.from_settings(settings)), capture_trace() as trace:
            started, cursor = perf_counter(), len(usage.events)
            try:
                validator = AnswerValidator()
                repaired = AnswerRepair(settings, guard_factory=guard_factory).repair(draft,
                    question="甲工艺温度是多少？", context=context, validator=validator,
                    validation=validator.validate(draft=draft, context=context, question="甲工艺温度是多少？"), scope=scope,
                    session=None, refresh_context=lambda: True)
                valid = validator.validate(draft=repaired, context=context, question="甲工艺温度是多少？").valid
                outcome = trace.stages["rag_routing"][-1].get("outcome")
                results.append(dict(kind="repair", outcome=outcome,
                    status="passed" if valid and outcome == "model_repaired" else "failed",
                    physical_calls=len(usage.events)-cursor, latency_ms=round((perf_counter()-started)*1000, 2)))
            except Exception as exc:
                results.append(dict(kind="repair", status="failed", error_type=type(exc).__name__))
    report = dict(synthetic_route_smoke=results, model_usage=summarize_usage(usage.events),
        limitation="Synthetic model interfaces only; trusted actor and evidence freshness are simulated. Not authenticated HTTP, real retrieval, or full answer-quality evaluation. Standalone usage not persisted to SQL.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if all(row["status"] == "passed" for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
