"""Actual HTTP upload/update/review/source/withdraw/delete acceptance in a NEW demo tenant.

The temporary TXT is synthetic and deleted on success. No original corpus file is edited.
Paid work is limited to two tiny indexing uploads; never retry QA/model requests here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.run_manufacturing_http_acceptance import LocalKeycloakSession


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--apply',action='store_true');p.add_argument('--allow-model-calls',action='store_true');a=p.parse_args(argv)
    run=a.run_dir.resolve();run.relative_to(ROOT/'tmp')
    if not a.apply or not a.allow_model_calls:p.error('真实验收需要--apply和--allow-model-calls')
    target=run/'document-lifecycle.json'
    if target.exists():raise ValueError('不可覆盖已完成的验收记录')
    cfg=json.loads((run/'state.json').read_text(encoding='utf-8'));kb=cfg['knowledge_bases']['生产工艺']
    s=LocalKeycloakSession(run/'tokens.json');base='http://127.0.0.1:8000/api/knowledge-bases/'+kb
    checks=[]
    def request(method,path,status=200,**kwargs):
        r=s.request(method,base+path,timeout=40,**kwargs)
        if r.status_code!=status:raise ValueError(f'{method} {path}: expected {status}, got {r.status_code}')
        return r
    def wait(job):
        started=time.monotonic()
        while time.monotonic()-started<600:
            data=request('GET','/indexing-jobs/'+job).json()
            if data['status']=='succeeded':return
            if data['status']=='failed':raise ValueError('验收文档索引失败')
            time.sleep(2)
        raise TimeoutError('验收文档索引超时')
    name='闭环验收临时资料_'+str(int(time.time()))+'.txt'
    content='合成演示资料，禁止用于生产。\nHC-DEMO在OP-X操作前检查防护罩。'.encode()
    uploaded=request('POST','/documents',202,files={'file':(name,content,'text/plain')}).json()
    doc_id=uploaded['document']['id'];path='/documents/'+doc_id
    (run/'lifecycle-pending.json').write_text(json.dumps(dict(document_id=doc_id,knowledge_base_id=kb)),encoding='utf-8')
    def review():return request('GET',path+'/review').json()
    def set_state(data,state):
        doc=data['document'];return request('PUT',path+'/publication',json=dict(expected_version=doc['version'],
            expected_revision=doc['publication']['revision'],state=state,review_note='助手使用合成TXT进行生命周期功能验收，非生产资料。')).json()
    wait(uploaded['indexing_job']['id']);data=review();chunk=data['items'][0]['id']
    assert data['document']['publication']['state']=='draft'
    request('GET',path+'/chunks/'+chunk,404);checks.append('draft_excluded_from_citation')
    first=set_state(data,'published');request('GET',path+'/chunks/'+chunk);checks.append('published_citation_available')
    request('PUT',path+'/publication',409,json=dict(expected_version=1,expected_revision=data['document']['publication']['revision'],
        state='withdrawn',review_note='验证过期复核冲突'))
    checks.append('stale_revision_rejected')
    raw=request('GET',path+'/source?version=1');assert hashlib.sha256(raw.content).hexdigest()==hashlib.sha256(content).hexdigest()
    content2=content+'\n更新：还应确认急停复位。'.encode()
    update=request('POST','/documents',202,files={'file':(name,content2,'text/plain')}).json()
    assert update['document']['id']==doc_id
    wait(update['indexing_job']['id']);new=review()
    assert new['document']['version']==2 and new['document']['publication']['state']=='draft'
    request('GET',path+'/source?version=1',409);request('GET',path+'/chunks/'+new['items'][0]['id'],404)
    checks.extend(['updated_version_requires_review','stale_source_version_rejected'])
    set_state(new,'published');raw=request('GET',path+'/source?version=2')
    assert hashlib.sha256(raw.content).hexdigest()==hashlib.sha256(content2).hexdigest()
    set_state(review(),'withdrawn');request('GET',path+'/chunks/'+new['items'][0]['id'],404)
    checks.extend(['current_source_hash_matches','withdrawn_citation_unavailable'])
    request('DELETE',path,204);request('GET',path+'/review',404)
    checks.append('temporary_document_deleted')
    result=dict(all_checks_passed=True,synthetic_fixture=True,reviewer='assistant_non_independent',document_id=doc_id,
        checks=checks,indexing_uploads=2,qa_requests=0)
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    return 0

if __name__=='__main__':raise SystemExit(main())
