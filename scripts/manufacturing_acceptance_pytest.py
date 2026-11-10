"""Opt-in private HTTP/model-input evidence for synthetic acceptance tests only."""
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path

import pytest

_request=ContextVar('synthetic_acceptance_http_request',default=None)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item,call):
    outcome=yield
    if call.when=='call':item.manufacturing_outcome=outcome.get_result().outcome


@pytest.fixture(autouse=True)
def synthetic_http_observation(request,monkeypatch):
    directory=os.environ.get('MANUFACTURING_ACCEPTANCE_DIR')
    if not directory:yield;return
    from fastapi.testclient import TestClient
    from manufacturing_m3_helpers import LocalGeneration
    from backend.app.evaluation.trace import capture_trace
    from backend.app.manufacturing.answer_context import current_manufacturing_context
    calls=[];models=[];original=TestClient.request;invoke=LocalGeneration.invoke
    def send(client,method,url,*args,**kwargs):
        row=dict(sequence=len(calls)+1,method=method,url=str(url),json=kwargs.get('json'))
        calls.append(row);token=_request.set(row['sequence'])
        try:
            response=original(client,method,url,*args,**kwargs)
            row.update(status=response.status_code,request_id=response.headers.get('x-request-id'),
                cache_control=response.headers.get('cache-control'),body=response.text)
            return response
        except Exception as exc:
            row['error_type']=type(exc).__name__;raise
        finally:_request.reset(token)
    def generate(model,messages):
        active=current_manufacturing_context()
        models.append(dict(request_sequence=_request.get(),work_order_id=active.context.record.work_order_id if active else None,
            messages=[dict(role=type(message).__name__,content=message.content) for message in messages]))
        return invoke(model,messages)
    monkeypatch.setattr(TestClient,'request',send);monkeypatch.setattr(LocalGeneration,'invoke',generate)
    with capture_trace() as trace:
        yield
    output=Path(directory);output.mkdir(parents=True,exist_ok=True)
    name=hashlib.sha256(request.node.nodeid.encode()).hexdigest()+'.json'
    with (output/name).open('x',encoding='utf-8') as stream:
        json.dump(dict(node_id=request.node.nodeid,outcome=getattr(request.node,'manufacturing_outcome','setup_failed'),
            transport='formal_asgi_http_handlers_with_local_models',http=calls,model_inputs=models,trace=trace.to_dict(),
            observations=getattr(request.node,'manufacturing_observations',None),
            limitation='Synthetic isolation only; no provider calls or real-user credentials.'),stream,ensure_ascii=False,indent=2,default=str)
