"""Small opt-in provider smoke using synthetic evidence, never business files."""
import argparse
import json
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from backend.app.config import Settings
from backend.app.knowledge.evidence_coordinator import AgentDecision, ModelEvidenceDecider
from backend.app.model_usage import summarize_usage, usage_context
from backend.app.security.retrieval_scope import RetrievalScope


def main():
    parser=argparse.ArgumentParser(description='使用三条合成资料检查 Agent 决策接口；调用已配置模型并产生费用。')
    parser.add_argument('--allow-model-calls',action='store_true')
    parser.add_argument('--output',type=Path,default=Path('data/ingestion-reports/agentic-rag/model-smoke.json'))
    args=parser.parse_args()
    if not args.allow_model_calls:
        parser.error('需要 --allow-model-calls；本工具会调用模型，不是离线测试')
    if args.output.exists():
        parser.error('报告已存在，请提供新的 --output，避免覆盖验收证据')
    settings=Settings()
    scenarios=[
        dict(question='甲工艺和乙工艺的温度分别是多少？',evidence=[
            dict(evidence_id='E1',content='甲工艺温度80摄氏度。'),dict(evidence_id='E2',content='乙工艺温度90摄氏度。')]),
        dict(question='甲工艺和乙工艺的温度分别是多少？',evidence=[dict(evidence_id='E1',content='甲工艺温度80摄氏度。')]),
        dict(question='甲工艺的完整操作条件是什么？',evidence=[dict(evidence_id='E1',content='本段为章节索引，完整操作条件紧接本段后面的正文。')]),
    ]
    results=[]
    with usage_context(scope=RetrievalScope(uuid4(),uuid4()),settings=settings) as usage:
        for index,scenario in enumerate(scenarios,1):
            started=perf_counter()
            scenario['executed_tools']=[]
            try:
                decision=AgentDecision.model_validate(ModelEvidenceDecider(settings).decide(
                    json.dumps(scenario,ensure_ascii=False),timeout=settings.agent_decision_timeout_seconds))
                if decision.action=='read' and decision.evidence_id not in {r['evidence_id'] for r in scenario['evidence']}:
                    raise ValueError('模型选择了未提供的锚点')
                result=dict(case=index,status='passed',action=decision.action,reason=decision.reason)
            except Exception as exc:
                result=dict(case=index,status='failed',error_type=type(exc).__name__)
            result['latency_ms']=round((perf_counter()-started)*1000,2)
            results.append(result)
    report=dict(synthetic_decision_smoke=results,model_usage=summarize_usage(usage.events),
        limitation='Synthetic decision interface only; not authenticated HTTP/answer-quality evaluation. No business source text sent. Ledger not persisted by this standalone synthetic run.')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False))
    return 0 if all(row['status']=='passed' for row in results) else 1


if __name__=='__main__':
    raise SystemExit(main())
