"""Explicit login, import, draft review plan and publish for the NEW M4 instance."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import sys
import time

import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_manufacturing_http_acceptance import LocalKeycloakSession, local_api_base
from backend.app.manufacturing.metadata import ManufacturingMetadata


def write_new(path,data):
    with path.open('x',encoding='utf-8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)


def quote_anchor(chunks, quote=None):
    for chunk in chunks:
        for parent in ((True,False) if quote is None else (False,True)):
            source=chunk.get('parent') if parent else chunk
            if not source:continue
            text=source['content']
            if quote is None:
                selected=text[:4000].strip()
            else:
                # Preserve the actual SQL bytes while matching only layout whitespace.
                indices=[i for i,c in enumerate(text) if not c.isspace()]
                compact=''.join(text[i] for i in indices)
                target=''.join(quote.split());start=compact.find(target)
                if start<0:continue
                selected=text[indices[start]:indices[start+len(target)-1]+1]
            if not selected or len(selected)>4000:continue
            result=dict(chunk_id=chunk['id'],vector_generation=chunk['vector_generation'],chunk_content_hash=chunk['content_hash'],
                quote=selected,quote_sha256=hashlib.sha256(selected.encode()).hexdigest())
            if parent:result.update(parent_id=source['id'],parent_content_hash=source['content_hash'])
            return result
    raise ValueError('无法在当前 SQL 子块或父块中完整定位来源，停止生成审核计划。')


def metadata_for(row,chunks,rules):
    role={'flow':'reference','form':'reference','record_rule':'reference','routing':'route','ecn':'change_notice','work_order_notice':'change_notice'}.get(row['document_role'],row['document_role'])
    kind='written_notice' if role=='change_notice' else 'historical_record' if role in ('event_record','inspection_record') else 'formal_document'
    data=dict(document_family_id=row['document_code'],document_code=row['document_code'],business_revision=row['business_revision'],
        document_role=role,source_kind=kind,scope_kind=('product_operation' if row['product_models'] and row['operation_codes'] else 'company') if row['scope_kind']=='routing' else row['scope_kind'],product_models=row['product_models'],operation_codes=row['operation_codes'],
        work_order_ids=row['work_order_ids'],effective_from=row.get('effective_from'),effective_to=row.get('effective_to'),
        plant_codes=row.get('plant_codes',[]),route_revisions=row.get('route_revisions',[]),
        archived=role=='instruction' and row.get('effective_to') is not None,source_anchors=[quote_anchor(chunks)],rules=[])
    if row['scope_kind']=='work_order':
        data['plant_codes']=sorted(set(re.findall(r'HC-P[0-9]+','\n'.join(chunk['content'] for chunk in chunks))))
    for rule in rules:
        if rule['source_document_id']!=row['id']:continue
        runtime={key:value for key,value in rule.items() if key not in ('source_document_id','source_quote','source_quote_sha256','review_state')}
        runtime['anchor']=quote_anchor(chunks,rule['source_quote']);data['rules'].append(runtime)
    return ManufacturingMetadata.model_validate(data).model_dump(mode='json')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['login','import','drafts','publish'])
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--credentials',type=Path)
    parser.add_argument('--reviewed-plan-sha256')
    parser.add_argument('--review-note')
    parser.add_argument('--plan-name',default='review-plan.json')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args(argv)
    run=args.run_dir.resolve();run.relative_to(ROOT/'tmp')
    if not re.fullmatch(r'review-plan(?:-[a-z0-9-]+)?\.json',args.plan_name):raise ValueError('审核计划须使用独立的本机文件名。')
    plan_path=run/args.plan_name
    state=json.loads((run/'state.json').read_text(encoding='utf-8'))
    if state['database']!='manufacturing_phase1_m4' or state['port'] in (8000,8001):raise ValueError('仅允许独立 M4 实例。')
    if not args.apply:print(json.dumps(dict(phase=args.phase,mode='dry_run',model_calls=0)));return 0
    if args.phase=='login':
        if args.credentials is None:raise ValueError('须指定已有身份凭据文件。')
        credentials=json.loads(args.credentials.read_text(encoding='utf-8-sig'));tokens={}
        if (run/'tokens.json').exists():raise ValueError('已有令牌文件，拒绝覆盖；刷新由会话管理。')
        for role,row in credentials.items():
            response=requests.post('http://127.0.0.1:8080/realms/enterprise-knowledge/protocol/openid-connect/token',
                data=dict(grant_type='password',client_id='enterprise-knowledge-cli',username=row['username'],password=row['password']),timeout=20)
            if response.status_code!=200:raise ValueError('演示账号认证失败。')
            payload=response.json();claims=json.loads(base64.urlsafe_b64decode(payload['access_token'].split('.')[1]+'=='))
            if claims['sub']!=row['subject']:raise ValueError('认证身份与独立数据库绑定不符。')
            tokens[role]=payload
        write_new(run/'tokens.json',tokens);print(json.dumps(dict(phase='login',identities=len(tokens),model_calls=0)));return 0
    base=local_api_base('http://127.0.0.1:'+str(state['port']))
    http=LocalKeycloakSession(run/'tokens.json')
    def request(method,path,**kwargs):
        response=http.request(method,base+path,timeout=30,**kwargs)
        if response.status_code>=400:raise ValueError('独立初始化 HTTP 失败：'+str(response.status_code)+' '+path)
        return response.json()
    manifest=json.loads((ROOT/'sample_docs/manufacturing_workorder_v1/manifest.json').read_text(encoding='utf-8'))
    rows=manifest['documents']
    if args.phase=='import':
        if (run/'document_map.json').exists() or (run/'import-requests.jsonl').exists():raise ValueError('已有完整或部分导入记录，拒绝重新上传。')
        for row in rows:
            if hashlib.sha256((ROOT/row['file']).read_bytes()).hexdigest()!=row['sha256']:raise ValueError('原件哈希变化。')
        documents={}
        for row in rows:
            kb=state['knowledge_bases'][row['group']]
            with (ROOT/row['file']).open('rb') as stream:
                result=request('POST',f'/api/knowledge-bases/{kb}/documents',files=dict(file=(Path(row['file']).name,stream)))
            documents[row['id']]=dict(knowledge_base_id=kb,document_id=result['document']['id'],file_name=Path(row['file']).name,sha256=row['sha256'])
            with (run/'import-requests.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(dict(source_id=row['id'],response=result),ensure_ascii=False)+'\n')
            print(json.dumps(dict(source_id=row['id'],status=result['document']['status'])),flush=True)
        write_new(run/'document_map.json',documents)
        return 0
    documents=json.loads((run/'document_map.json').read_text(encoding='utf-8'))
    if args.phase=='drafts':
        if plan_path.exists():raise ValueError('已有审核计划，拒绝覆盖。')
        rules=json.loads((ROOT/'sample_docs/manufacturing_workorder_v1/rules.json').read_text(encoding='utf-8'))['rules']
        plans=[]
        for row in rows:
            identity=documents[row['id']];kb=identity['knowledge_base_id'];doc=identity['document_id']
            chunks=[];after=-1
            while True:
                page=request('GET',f'/api/knowledge-bases/{kb}/documents/{doc}/review',params=dict(include_parents='true',after_index=after,limit=50))
                if page['document']['status']!='ready':raise ValueError('资料未完成索引，不能生成审核计划。')
                chunks.extend(page['items'])
                if page['next_after'] is None:break
                after=page['next_after']
            plans.append(dict(source_id=row['id'],**identity,document=page['document'],metadata=metadata_for(row,chunks,rules),chunks=chunks))
        write_new(plan_path,dict(reviewer='pending',documents=plans))
        sha=hashlib.sha256(plan_path.read_bytes()).hexdigest()
        print(json.dumps(dict(phase='drafts',documents=len(plans),review_plan_sha256=sha,model_calls=0)));return 0
    if hashlib.sha256(plan_path.read_bytes()).hexdigest()!=args.reviewed_plan_sha256 or not args.review_note or len(args.review_note.strip())<10:
        raise ValueError('发布须携带已人工核对的计划哈希和非独立复核说明。')
    if (run/'publication-results.json').exists():raise ValueError('已有发布结果，拒绝重复执行。')
    results=[]
    for row in json.loads(plan_path.read_text(encoding='utf-8'))['documents']:
        kb=row['knowledge_base_id'];doc=row['document_id'];path=f'/api/knowledge-bases/{kb}/documents/{doc}'
        current=request('GET',path+'/manufacturing-metadata')
        if current['manufacturing'] is not None:raise ValueError('资料已有制造标注，不自动覆盖。')
        body=dict(expected_document_version=row['document']['version'],expected_revision=0,state='draft',metadata=row['metadata'],review_note=args.review_note)
        draft=request('PUT',path+'/manufacturing-metadata',json=body)
        reviewed=request('PUT',path+'/manufacturing-metadata',json=dict(body,expected_revision=draft['revision'],state='reviewed'))
        published=request('PUT',path+'/publication',json=dict(expected_version=reviewed['document']['version'],
            expected_revision=reviewed['document']['publication']['revision'],state='published',review_note=args.review_note,acknowledge_ocr=True))
        result=dict(source_id=row['source_id'],review=reviewed,publication=published,reviewer='assistant_non_independent',plan_sha256=args.reviewed_plan_sha256)
        results.append(result)
        with (run/'publication-requests.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(result,ensure_ascii=False)+'\n')
    write_new(run/'publication-results.json',results);print(json.dumps(dict(phase='publish',documents=len(results),model_calls=0)));return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        print(json.dumps(dict(status='failed',error_type=type(exc).__name__,message='初始化中断，检查已保留的阶段记录；不自动重试。')),file=sys.stderr)
        raise SystemExit(1) from None
