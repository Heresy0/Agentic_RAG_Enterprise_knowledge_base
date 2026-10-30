import json
import sys
from types import SimpleNamespace

from backend.app.knowledge.answer_repair import AnswerRepair
from backend.app.knowledge.query_planner import QueryPlan, QueryPlanner
from scripts import verify_rag_routes


def test_route_smoke_uses_current_planner_and_repair_contracts_without_network(monkeypatch, tmp_path):
    settings = SimpleNamespace(chat_model='fake', chat_base_url='https://fake.invalid', chat_api_key='fake',
        rag_planner_mode='auto', rag_planner_output_tokens=768, rag_planner_timeout_seconds=8,
        rag_repair_enabled=True, rag_repair_prompt_bytes=24000, rag_repair_output_tokens=1536,
        rag_repair_timeout_seconds=8)

    def factory(**kwargs):
        def structured(schema, **kwargs):
            def invoke(messages):
                if schema is QueryPlan:
                    return QueryPlan(intent='comparison', strategy='agentic', reason='multiple_aspects',
                        subquestions=[dict(question='甲工艺的温度', source_quote='甲工艺'),
                                      dict(question='乙工艺的温度', source_quote='乙工艺')])
                draft = json.loads(messages[-1].content)['draft']
                draft['coverage'][0]['claim_indices'] = [1]
                return schema.model_validate(draft)
            return SimpleNamespace(invoke=invoke)
        return SimpleNamespace(with_structured_output=structured)

    monkeypatch.setattr(verify_rag_routes, 'Settings', lambda: settings)
    monkeypatch.setattr(verify_rag_routes, 'QueryPlanner',
                        lambda settings, **kwargs: QueryPlanner(settings, model_factory=factory, **kwargs))
    monkeypatch.setattr(verify_rag_routes, 'AnswerRepair',
                        lambda settings, **kwargs: AnswerRepair(settings, model_factory=factory, **kwargs))
    output = tmp_path / 'route-smoke.json'
    monkeypatch.setattr(sys, 'argv', ['verify_rag_routes', '--allow-model-calls', '--output', str(output)])
    assert verify_rag_routes.main() == 0
    results = json.loads(output.read_text(encoding='utf-8'))['synthetic_route_smoke']
    assert len(results) == 4 and all(row['status'] == 'passed' for row in results)
    assert results[-1]['outcome'] == 'model_repaired'
