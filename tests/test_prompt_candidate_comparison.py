import copy
from types import SimpleNamespace

import pytest

from scripts import compare_manufacturing_prompt_candidate as comparison


@pytest.mark.parametrize('failure',[None,'timeout','access'])
def test_paid_comparison_preserves_inputs_accounts_attempts_and_closes(tmp_path,monkeypatch,failure):
    import httpx
    import openai
    payload=dict(model=comparison.MODEL,reasoning_effort='none',temperature=0,
        extra_body={'enable_thinking':False},response_format={'type':'json_object'},
        messages=[dict(role='system',content='original'+comparison.MARKER+'fixed contract'),dict(role='user',content='fixed source')])
    candidate=comparison.candidate_payload(payload)
    assert candidate['messages'][1:]==payload['messages'][1:]
    assert candidate['messages'][0]['content'].endswith(comparison.MARKER+'fixed contract')
    entry=dict(case_id='MFG-GOLD-006',payloads=dict(current=payload,candidate=candidate),fixed_input_sha256='fixed')
    comparison.write(tmp_path,'inputs.json',[entry])
    comparison.write(tmp_path,'plan.json',dict(model=comparison.MODEL,variants=comparison.VARIANTS,limits=comparison.LIMITS,
        input_sha256=comparison.digest(tmp_path/'inputs.json'),protected={}))
    credentials=tmp_path/'test.env'
    credentials.write_text('DASHSCOPE_MODEL=qwen3.7-flash\nDASHSCOPE_API_KEY=test-only\nDASHSCOPE_BASE_URL=https://unused.invalid/v1\n')
    calls=[]
    def generate(**request):
        calls.append(copy.deepcopy(request))
        if failure=='timeout':raise openai.APITimeoutError(request=httpx.Request('POST','https://unused.invalid'))
        if failure=='access':raise openai.NotFoundError('unavailable',response=httpx.Response(404,request=httpx.Request('POST','https://unused.invalid')),body=None)
        return SimpleNamespace(model=request['model'],usage=SimpleNamespace(model_dump=lambda:dict(prompt_tokens=2,completion_tokens=1,total_tokens=3)),
            _request_id='test-id',choices=[SimpleNamespace(message=SimpleNamespace(content='{}',refusal=None),finish_reason='stop')])
    monkeypatch.setattr(openai,'OpenAI',lambda **kwargs:SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=generate)),close=lambda:None))
    comparison.run(tmp_path,credentials)
    closed=comparison.read(tmp_path/'closed.json');count=1 if failure=='access' else 6
    assert closed['completed']==closed['budget']['calls']==len(calls)==count
    assert closed['pending']==0 and closed['unused_allowance_reusable'] is False
    assert closed['budget']['charged_tokens']==count*(32000 if failure else 3)
    if failure!='access':assert calls==[payload,candidate,candidate,payload,payload,candidate]
    assert all({k:v for k,v in c.items() if k!='messages'}=={k:v for k,v in payload.items() if k!='messages'} for c in calls)
    assert all(c['messages'][1:]==payload['messages'][1:] for c in calls)
    with pytest.raises(FileExistsError):comparison.run(tmp_path,credentials)
    assert len(calls)==count


def test_changed_frozen_request_stops_before_client_or_credentials(tmp_path):
    comparison.write(tmp_path,'inputs.json',[])
    comparison.write(tmp_path,'plan.json',dict(model=comparison.MODEL,variants=comparison.VARIANTS,
        limits=comparison.LIMITS,input_sha256='changed'))
    with pytest.raises(AssertionError):comparison.run(tmp_path,tmp_path/'absent.env')
    assert not (tmp_path/'started.json').exists()
