import copy
from types import SimpleNamespace

import pytest

from scripts import compare_manufacturing_generation_models as comparison


@pytest.mark.parametrize('failure', [None, 'timeout', 'access'])
def test_paired_inputs_budget_failures_and_closed_batch(tmp_path, monkeypatch, failure):
    import httpx
    import openai
    payload=dict(model=comparison.MODELS[0],messages=[dict(role='user',content='fixed')],
                 extra_body={'enable_thinking':False},reasoning_effort='none')
    entry=dict(case_id='MFG-GOLD-004',payload=payload,
               shared_input_sha256=comparison.hashed({k:v for k,v in payload.items() if k!='model'}))
    comparison.write(tmp_path,'inputs.json',[entry])
    comparison.write(tmp_path,'plan.json',dict(models=comparison.MODELS,limits=comparison.LIMITS,
        input_sha256=comparison.digest(tmp_path/'inputs.json'),protected={}))
    credentials=tmp_path/'test.env'
    credentials.write_text('DASHSCOPE_MODEL=qwen3.7-flash\nDASHSCOPE_API_KEY=test-only\nDASHSCOPE_BASE_URL=https://unused.invalid/v1\n')
    calls=[]
    def generate(**request):
        calls.append(copy.deepcopy(request))
        if failure=='timeout':
            raise openai.APITimeoutError(request=httpx.Request('POST','https://unused.invalid'))
        if failure=='access':
            raise openai.NotFoundError('unavailable',response=httpx.Response(404,request=httpx.Request('POST','https://unused.invalid')),body=None)
        return SimpleNamespace(model=request['model'],usage=SimpleNamespace(model_dump=lambda:dict(prompt_tokens=2,completion_tokens=1,total_tokens=3)),
            _request_id='test-id',choices=[SimpleNamespace(message=SimpleNamespace(content='{}',refusal=None),finish_reason='stop')])
    monkeypatch.setattr(openai,'OpenAI',lambda **kwargs:SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=generate)),close=lambda:None))
    comparison.run(tmp_path,credentials)
    closed=comparison.read(tmp_path/'closed.json')
    count=1 if failure=='access' else 6
    assert closed['completed']==closed['budget']['calls']==len(calls)==count
    assert closed['pending']==0 and closed['unused_allowance_reusable'] is False
    assert closed['budget']['charged_tokens']==count*(32000 if failure else 3)
    assert all({k:v for k,v in c.items() if k!='model'}=={k:v for k,v in payload.items() if k!='model'} for c in calls)
    if failure!='access': assert [c['model'] for c in calls]==list(comparison.MODELS+comparison.MODELS[::-1]+comparison.MODELS)
    with pytest.raises(FileExistsError):comparison.run(tmp_path,credentials)
    assert len(calls)==count
