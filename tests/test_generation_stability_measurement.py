import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from scripts import measure_manufacturing_generation_stability as measurement


@pytest.mark.parametrize('connection_failure', [False, True])
def test_fixed_experiment_counts_every_attempt_and_never_retries(tmp_path, monkeypatch, connection_failure):
    from openai import APIConnectionError
    import httpx
    import openai
    from backend.app.knowledge import reranker
    from backend.app.model_usage import ModelCall

    entries = [dict(case_id=f'MFG-GOLD-{n:03}', kind='generation', request_sha256=str(n),
                    payload=dict(model='test', messages=[dict(role='user', content=str(n))]))
               for n in measurement.CASES]
    entries += [dict(case_id=f'MFG-GOLD-{n:03}', kind='rerank_probe', request_sha256=str(n),
                     query=str(n), documents=['fixed document']) for n in measurement.RERANK_CASES]
    measurement.write(tmp_path, 'inputs.json', entries)
    measurement.write(tmp_path, 'plan.json', dict(input_sha256=measurement.digest(tmp_path/'inputs.json'),
                      limits=measurement.LIMITS, repeats=3, protected={}))
    credentials=tmp_path/'test.env'
    credentials.write_text('DASHSCOPE_MODEL=qwen3.7-flash\nDASHSCOPE_RERANK_MODEL=qwen3-rerank\n'
        'DASHSCOPE_API_KEY=test-only\nDASHSCOPE_BASE_URL=https://unused.invalid/v1\n'
        'DASHSCOPE_RERANK_URL=https://unused.invalid/rerank\nRERANK_TIMEOUT_SECONDS=5\n')
    calls=[]
    def generate(**payload):
        calls.append(copy.deepcopy(payload))
        if connection_failure:
            raise APIConnectionError(request=httpx.Request('POST', 'https://unused.invalid'))
        return SimpleNamespace(usage=SimpleNamespace(model_dump=lambda: dict(total_tokens=3)),
            _request_id='supplier-id', choices=[SimpleNamespace(message=SimpleNamespace(content='{}', refusal=None),finish_reason='stop')])
    monkeypatch.setattr(openai, 'OpenAI', lambda **kwargs: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=generate)),close=lambda: None))
    class FixedReranker:
        def __init__(self, **kwargs):pass
        def rerank(self, **kwargs):
            with ModelCall('rerank','test') as call:call.observe(dict(total_tokens=2))
            return []
    monkeypatch.setattr(reranker,'QwenReranker',FixedReranker)
    measurement.run(tmp_path,credentials)
    closed=measurement.read(tmp_path/'closed.json');rows=measurement.read(tmp_path/'results.json')
    assert closed['completed']==closed['budget']['calls']==45 and closed['pending']==0
    assert closed['budget']['unknown_calls']==(36 if connection_failure else 0)
    assert closed['budget']['charged_tokens']==(36*32000+9*2 if connection_failure else 36*3+9*2)
    assert len(calls)==36 and calls[:12]==calls[12:24]==calls[24:]
    assert sum(bool(r.get('error_type')) for r in rows)==(36 if connection_failure else 0)
    assert all(len(r['usage'])==1 for r in rows)
    assert closed['unused_allowance_reusable'] is False
    with pytest.raises(FileExistsError):measurement.run(tmp_path,credentials)
    assert len(calls)==36


def test_frozen_input_changes_stop_before_any_model_client(tmp_path):
    measurement.write(tmp_path,'inputs.json',[])
    measurement.write(tmp_path,'plan.json',dict(input_sha256='invalid',limits=measurement.LIMITS))
    with pytest.raises(AssertionError):measurement.run(tmp_path,tmp_path/'not-read.env')
    assert not (tmp_path/'started.json').exists()
