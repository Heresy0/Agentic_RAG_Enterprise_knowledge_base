import json
from pathlib import Path
import pytest
from scripts.prepare_manufacturing_phase1 import main, ROOT


def test_preparation_is_read_only_by_default(tmp_path,capsys,monkeypatch):
    path=tmp_path/'subjects.json'
    path.write_text(json.dumps({'知识管理员':{'subject':'synthetic-admin'}}),encoding='utf-8')
    target=ROOT/'tmp/manufacturing-phase1-m4/dry-run-must-not-create'
    def forbidden(*args,**kwargs): raise AssertionError('dry-run cannot connect to a database')
    monkeypatch.setattr('scripts.prepare_manufacturing_phase1.create_engine',forbidden)
    assert main(['--output-dir',str(target),'--credentials',str(path)])==0
    result=json.loads(capsys.readouterr().out)
    assert result['model_calls']==0 and not result['stable_instances_modified']
    assert not target.exists()


@pytest.mark.parametrize('port',['8000','8001','0'])
def test_preparation_rejects_stable_ports_without_accessing_credentials(tmp_path,port):
    with pytest.raises(ValueError):main(['--output-dir',str(ROOT/'tmp/invalid'),'--credentials',str(tmp_path/'absent'),'--port',port])


def test_review_anchor_preserves_sql_bytes_and_uses_complete_parent():
    import hashlib
    from scripts.initialize_manufacturing_phase1 import quote_anchor
    chunk=dict(id='a'*64,vector_generation='b'*32,content_hash='c'*64,content='标题',
        parent=dict(id='d'*64,content_hash='e'*64,content='文件身份\n适用范围：HC-P01\n完整 规则\n不得放行'))
    whole=quote_anchor([chunk])
    assert whole['quote']==chunk['parent']['content'] and whole['parent_id']=='d'*64
    matched=quote_anchor([chunk],'完整规则')
    assert matched['quote']=='完整 规则'
    assert matched['quote_sha256']==hashlib.sha256('完整 规则'.encode()).hexdigest()
    with pytest.raises(ValueError):quote_anchor([chunk],'允许放行')


def test_official_batch_sizes_and_deadline_are_fixed_before_model_calls():
    from scripts.accept_manufacturing_phase1 import AcceptanceBudget,select_cases
    from backend.app.quality.eval_budget import EvaluationStopped
    assert len(select_cases('development'))==16 and len(select_cases('formal'))==40
    budget=AcceptanceBudget(dict(max_cases=1,max_model_calls=1,max_tokens=32000,reservation_tokens=32000,max_seconds=30))
    budget.request_deadline=0
    with pytest.raises(EvaluationStopped):budget.before_call()
    assert budget.calls==0 and budget.limited_by=='request_time'


def test_new_pkce_client_does_not_copy_secrets_ids_or_old_redirects():
    from scripts.configure_manufacturing_phase1_login import public_client
    source=dict(id='old-client-id',secret='must-not-copy',publicClient=True,redirectUris=['http://127.0.0.1:8000/'],
        protocolMappers=[dict(id='old-mapper-id',name='audience',protocolMapper='oidc-audience-mapper',config={'included.client.audience':'enterprise-knowledge-api'})])
    client=public_client(source,8002)
    assert client['redirectUris']==['http://127.0.0.1:8002/']
    assert client['attributes']['pkce.code.challenge.method']=='S256'
    assert not client['directAccessGrantsEnabled'] and 'secret' not in client and 'id' not in client
    assert 'id' not in client['protocolMappers'][0] and source['id']=='old-client-id'
