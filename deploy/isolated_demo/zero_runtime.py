"""Deployment-only guard: every HTTP request has a hard zero physical-model budget."""
import json,os
from pathlib import Path
from uuid import uuid4
from fastapi.responses import JSONResponse
from backend.app.main import app
from backend.app.quality.eval_budget import EvaluationBudget,evaluation_budget
from backend.app.evaluation.trace import capture_trace

@app.middleware('http')
async def zero_model_budget(request,call_next):
    budget=EvaluationBudget(dict(max_model_calls=0,max_tokens=0,reservation_tokens=32000,max_seconds=180))
    with evaluation_budget(budget),capture_trace() as trace:
        if request.method=='POST' and request.url.path=='/api/quality/evaluations':
            body=await request.json()
            if body.get('allow_model_calls') or body.get('max_model_calls')!=0 or body.get('max_tokens')!=0:
                return JSONResponse(status_code=422,content={'detail':'候选环境仅接受零模型预算任务。'})
        response=await call_next(request)
    response.headers['X-Candidate-Model-Call-Limit']='0'
    if request.url.path.startswith('/api/'):
        item=dict(path=request.url.path,method=request.method,status=response.status_code,
            request_id=response.headers.get('X-Request-ID'),budget=budget.snapshot(),trace=trace.to_dict())
        Path(os.environ['CANDIDATE_TELEMETRY_DIR'],uuid4().hex+'.json').write_text(json.dumps(item,ensure_ascii=False),encoding='utf-8')
    return response
