"""Local isolated manufacturing operations. Writes need --apply; embeddings also need --allow-model-calls.
Credentials and raw snapshots are confined to ignored tmp/. Existing corpora are never rewritten.
"""
import argparse, hashlib, json, mimetypes, os, secrets, sys, time
from collections import Counter
from pathlib import Path
from uuid import uuid4
import requests
from dotenv import dotenv_values
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
RUN=None
ENV={}
BASE='http://127.0.0.1:8000'
CORPUS=ROOT/'sample_docs/manufacturing_zh_v3'
TABLES=['tenants','users','knowledge_bases','knowledge_base_user_grants','knowledge_base_department_grants',
        'documents','document_chunks','document_parents','conversations']
def sha(b):return hashlib.sha256(b).hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def dump(name,value):
    path=RUN/name
    path.write_text(json.dumps(value,ensure_ascii=False,default=str,indent=2)+'\n',encoding='utf-8')
def engine():
    from backend.app.db.session import create_database_engine
    return create_database_engine(ENV['DATABASE_URL'])
def state():return read(RUN/'state.json')
def snapshot(name):
    from sqlalchemy import text
    target=RUN/(name+'.json');assert not target.exists()
    db=engine();result={}
    excluded=state()['tenant_id'] if (RUN/'state.json').exists() else None
    with db.connect().execution_options(isolation_level='REPEATABLE READ') as c,c.begin():
        c.execute(text('SET TRANSACTION READ ONLY'))
        for table in TABLES:
            field='id' if table=='tenants' else 'tenant_id'
            clause=f' WHERE {field} != CAST(:tid AS uuid)' if excluded else ''
            result[table]=sorted(c.execute(text(f'SELECT md5(to_jsonb(t)::text) FROM {table} t'+clause),{'tid':excluded}).scalars())
        result['migration']=sorted(c.execute(text('SELECT version_num FROM alembic_version')).scalars())
        result['active_jobs']=c.execute(text("SELECT count(*) FROM document_indexing_jobs WHERE status IN ('queued','running','retry_wait')")).scalar()
    db.dispose();dump(name+'.json',result)
    file_snapshot(name)
    print(json.dumps(dict(snapshot=name,counts={k:len(v) for k,v in result.items() if isinstance(v,list)},active_jobs=result['active_jobs'])),flush=True)
def provision():
    from sqlalchemy.orm import Session
    from scripts.provision_manufacturing_demo import create_demo
    from scripts.manufacturing_corpus_data import ROLE_PERMISSIONS
    assert not (RUN/'state.json').exists() and not (RUN/'credentials.json').exists()
    if not (RUN/'files-before.json').exists():raise ValueError('须先完成数据库与文件基线检查')
    assert read(RUN/'before.json')['active_jobs']==0
    r=requests.post('http://127.0.0.1:8080/realms/master/protocol/openid-connect/token',data={
        'grant_type':'password','client_id':'admin-cli','username':ENV['KEYCLOAK_ADMIN_USERNAME'],
        'password':ENV['KEYCLOAK_ADMIN_PASSWORD']},timeout=20);r.raise_for_status()
    s=requests.Session();s.headers['Authorization']='Bearer '+r.json()['access_token']
    suffix=uuid4().hex[:8];credentials={};subjects={}
    for role,name in zip(ROLE_PERMISSIONS,['operator','process','quality','admin','reference']):
        username='mfgzh-'+name+'-'+suffix;password=secrets.token_urlsafe(24)
        r=s.post('http://127.0.0.1:8080/admin/realms/enterprise-knowledge/users',json=dict(
            username=username,enabled=True,emailVerified=True,firstName='中文演示',lastName=role,email=username+'@example.invalid',
            credentials=[dict(type='password',value=password,temporary=False)]),timeout=20);r.raise_for_status()
        subject=r.headers['Location'].rsplit('/',1)[-1]
        credentials[role]=dict(username=username,password=password,subject=subject);subjects[role]=subject
        dump('credentials.json',credentials)
    db=engine()
    with Session(db) as session,session.begin():
        result=create_demo(session,tenant_name='制造业闭环隔离演示_'+suffix,subjects=subjects)
    db.dispose();dump('state.json',result);print(json.dumps(dict(created=True,**result),ensure_ascii=False),flush=True)
def tokens():
    results={}
    for role,row in read(RUN/'credentials.json').items():
        r=requests.post('http://127.0.0.1:8080/realms/enterprise-knowledge/protocol/openid-connect/token',
            data=dict(grant_type='password',client_id='enterprise-knowledge-cli',username=row['username'],password=row['password']),timeout=20)
        r.raise_for_status();results[role]={k:v for k,v in r.json().items() if k in ('access_token','refresh_token','expires_in','refresh_expires_in')}
    dump('tokens.json',results);print(json.dumps(dict(tokens=len(results),minimum_ttl=min(r['expires_in'] for r in results.values()))),flush=True)
def client():
    from scripts.run_manufacturing_http_acceptance import LocalKeycloakSession
    return LocalKeycloakSession(RUN/'tokens.json')
def acl():
    from scripts.manufacturing_corpus_data import ROLE_PERMISSIONS
    cfg=state();checks=[]
    from sqlalchemy import text
    db=engine()
    with db.connect() as c:foreign=str(c.execute(text('SELECT id FROM knowledge_bases WHERE tenant_id != CAST(:tid AS uuid) LIMIT 1'),{'tid':cfg['tenant_id']}).scalar())
    db.dispose()
    for role,permissions in ROLE_PERMISSIONS.items():
        s=requests.Session();s.headers['Authorization']='Bearer '+read(RUN/'tokens.json')[role]['access_token']
        r=s.get(BASE+'/api/knowledge-bases',timeout=20);r.raise_for_status()
        checks.append(dict(role=role,check='effective_permissions',passed={d['id']:d['permission'] for d in r.json()['items']}=={cfg['knowledge_bases'][g]:p for g,p in permissions.items()}))
        for group,kb in cfg['knowledge_bases'].items():
            r=s.get(BASE+'/api/knowledge-bases/'+kb+'/documents',timeout=20)
            checks.append(dict(role=role,check='read_'+group,actual=r.status_code,passed=r.status_code==(200 if group in permissions else 403)))
        r=s.get(BASE+'/api/knowledge-bases/'+foreign+'/documents',timeout=20)
        checks.append(dict(role=role,check='cross_tenant_denied',actual=r.status_code,passed=r.status_code==403))
    dump('acl.json',checks);assert all(r['passed'] for r in checks)
    print(json.dumps(dict(acl_checks=len(checks),passed=True)),flush=True)
def upload():
    from scripts.validate_manufacturing_zh import validate_freeze
    from scripts.validate_manufacturing_public import inventory
    frozen,_,_=validate_freeze(ROOT/'evals/manufacturing_zh_v3',CORPUS);manifest=inventory(CORPUS)
    profile = selected_profile()
    if profile:
        allowed={row['id']for row in profile['documents']}
        manifest['documents']=[row for row in manifest['documents']if row['id']in allowed]
    cfg=state();s=client();assert not (RUN/'uploads.json').exists()
    for kb in cfg['knowledge_bases'].values():
        r=s.get(BASE+'/api/knowledge-bases/'+kb+'/documents',timeout=20);r.raise_for_status();assert not r.json()['items']
    rows=[];dump('uploads.json',rows)
    for source in manifest['documents']:
        path=CORPUS/source['file'];assert sha(path.read_bytes())==source['sha256']
        with path.open('rb') as f:
            r=s.post(BASE+'/api/knowledge-bases/'+cfg['knowledge_bases'][source['group']]+'/documents',
                     files={'file':(path.name,f,mimetypes.guess_type(path.name)[0] or 'application/octet-stream')},timeout=90)
        r.raise_for_status();result=r.json()
        rows.append(dict(source_id=source['id'],file_name=path.name,sha256=source['sha256'],
            document_id=result['document']['id'],job_id=result['indexing_job']['id'],http_status=r.status_code,
            request_id=r.headers.get('X-Request-ID')));dump('uploads.json',rows)
        print(json.dumps(dict(uploaded=source['id'],count=len(rows),status=r.status_code)),flush=True)
    dump('system_config.json',dict(base_url=BASE,**cfg,corpus_manifest_sha256=frozen['corpus_manifest_sha256'],
        **({'profile_file':str(ARGS.profile.resolve()),'profile_sha256':sha(ARGS.profile.read_bytes())}if profile else {})))
def status():
    s=client();docs=[];jobs=[]
    for kb in state()['knowledge_bases'].values():
        r=s.get(BASE+'/api/knowledge-bases/'+kb+'/documents',timeout=20);r.raise_for_status();docs+=r.json()['items']
        r=s.get(BASE+'/api/knowledge-bases/'+kb+'/indexing-jobs',timeout=20);r.raise_for_status();jobs+=r.json()['items']
    dump('index-progress.json',dict(documents=docs,jobs=jobs))
    print(json.dumps(dict(documents=dict(Counter(r['status'] for r in docs)),jobs=dict(Counter(r['status'] for r in jobs)),
        chunks=sum(r['chunk_count'] for r in docs),failures=[dict(id=j['document_id'],error=j.get('last_error')) for j in jobs if j['status']=='failed'])),flush=True)
def preserve():
    a,b=read(RUN/'before.json'),read(RUN/'after.json')
    checks={k:a[k]==b[k] for k in TABLES+['migration']}
    assert all(checks.values())
    assert read(RUN/'files-before.json') == read(RUN/'files-after.json')
    dump('preservation.json',dict(all_checks_passed=True,tables=checks,excluded_new_tenant=state()['tenant_id'],
        before_sha256=sha((RUN/'before.json').read_bytes()),after_sha256=sha((RUN/'after.json').read_bytes())))
    print(json.dumps(dict(original_tables_unchanged=True,frozen_files_unchanged=True,checks=len(checks))),flush=True)

def file_snapshot(name):
    import subprocess
    # The initial Git base, not the current mutable filesystem, defines the lock.
    reference_path=RUN/'baseline-ref.json'
    if name=='before':
        if reference_path.exists():
            if (RUN/'files-before.json').exists()or read(reference_path)['ref']!=ARGS.baseline_ref:raise ValueError('基线引用记录已存在')
        else:dump(reference_path.name,dict(ref=ARGS.baseline_ref))
    reference=read(reference_path)['ref']if reference_path.exists()else '8b6ed0c'
    entries=subprocess.run(['git','ls-tree','-rz','--full-tree',reference],cwd=ROOT,check=True,capture_output=True).stdout
    locked={row['path']for row in read(RUN/'files-before.json')}if name=='after'and (RUN/'files-before.json').exists()else None
    rows=[]
    for entry in entries.split(b'\0'):
        if not entry:continue
        head,path=entry.split(b'\t',1)
        if not path.startswith((b'sample_docs/',b'evals/')):continue
        if locked is not None and path.decode()not in locked:continue
        raw=(ROOT/path.decode()).read_bytes()
        actual=hashlib.sha1(('blob '+str(len(raw))+'\0').encode()+raw).hexdigest()
        if actual != head.split()[2].decode():
            # Respect repository clean filters (notably Windows CRLF). Raw byte
            # hashes below still compare the actual before/after file contents.
            actual=subprocess.run(['git','hash-object','--path='+path.decode(),path.decode()],cwd=ROOT,
                capture_output=True,check=True,text=True).stdout.strip()
            if actual!=head.split()[2].decode():raise ValueError('冻结资料已变化：'+path.decode())
        rows.append(dict(path=path.decode(),sha256=sha(raw)))
    dump('files-'+name+'.json',rows)

def publish():
    if not ARGS.review_note.strip():raise ValueError('发布必须明确填写复核说明')
    s=client();cfg=state();reviewed=[]
    for group,kb in cfg['knowledge_bases'].items():
        r=s.get(BASE+'/api/knowledge-bases/'+kb+'/documents',timeout=20);r.raise_for_status()
        for doc in r.json()['items']:
            if doc['status']!='ready':continue
            url=BASE+'/api/knowledge-bases/'+kb+'/documents/'+doc['id']
            preview=s.get(url+'/review',timeout=20);preview.raise_for_status();data=preview.json()
            original=s.get(url+'/source',params={'version':doc['version']},timeout=30);original.raise_for_status()
            if sha(original.content)!=data['document']['content_hash']:raise ValueError('原件指纹变化')
            result=s.put(url+'/publication',json=dict(expected_version=doc['version'],
                expected_revision=data['document']['publication'].get('revision',0),state='published',
                review_note=ARGS.review_note,acknowledge_ocr=ARGS.acknowledge_ocr),timeout=20)
            result.raise_for_status();reviewed.append(result.json())
    dump('publication-review.json',reviewed)
    print(json.dumps(dict(published=len(reviewed),review_type='operator_explicit_review_note'),ensure_ascii=False))

def audit():
    import subprocess
    from scripts.validate_manufacturing_public import parser_fingerprint
    cfg=state()
    profile=selected_profile()
    expected=[row['id']for row in profile['documents']]if profile else None
    result=subprocess.run(['docker','exec','-i','enterprise-knowledge-api','python','-',cfg['tenant_id']]+([json.dumps(expected)]if expected is not None else []),
        # Explicit manifest selection is passed as an argument; no arbitrary SQL or shell input.
        input=(ROOT/'scripts/audit_manufacturing_index.py').read_bytes(),capture_output=True,check=True)
    data=json.loads(result.stdout)
    if data['parser_sha256']!=parser_fingerprint():raise ValueError('容器解析代码与工作区不一致')
    manifest=read(CORPUS/'manifest.json');sources={row['id']:row for row in manifest['documents']}
    for row in data['records']:
        source=sources[row['source_id']]
        if row['sha256']!=source['sha256'] or row['knowledge_base_id']!=cfg['knowledge_bases'][source['group']]:
            raise ValueError('索引来源指纹或权限分组变化')
    if expected is not None and {r['source_id']for r in data['records']}!=set(expected):raise ValueError('演示资料范围不符')
    runtime=[]
    for service in ('api','worker','quality-worker'):
        container=subprocess.run(['docker','compose','-f','compose.yaml','-f','compose.manufacturing.yaml','ps','-q',service],
            cwd=ROOT,capture_output=True,check=True,text=True).stdout.strip()
        if not container:raise ValueError('服务尚未运行：'+service)
        image=subprocess.run(['docker','inspect','--format','{{.Image}}',container],capture_output=True,check=True,text=True).stdout.strip()
        runtime.append(dict(service=service,image_id=image))
    if len({r['image_id'] for r in runtime})!=1:raise ValueError('API与两类Worker镜像不一致')
    target=RUN/ARGS.audit_name
    if target.parent!=RUN or target.suffix!='.json':raise ValueError('审计文件名必须是当前运行目录下的JSON文件')
    if target.exists():raise ValueError('审计已存在，请使用新的运行目录，避免覆盖证据')
    dump(target.name,data);dump(target.stem+'-runtime.json',runtime)
    config=read(RUN/'system_config.json')
    config.update(index_audit_file=str(target),index_audit_sha256=sha(target.read_bytes()))
    dump('system_config.json',config)
    print(json.dumps(dict(documents=data['documents'],ready=data['ready_documents'],chunks=data['chunks'],runtime_consistent=True)))

def selected_profile():
    if ARGS.profile:
        from scripts.build_manufacturing_demo_profile import load_profile
        return load_profile(ARGS.profile)
    config=RUN/'system_config.json'
    if config.exists() and read(config).get('profile_file'):
        from scripts.build_manufacturing_demo_profile import load_profile
        path=Path(read(config)['profile_file'])
        if sha(path.read_bytes())!=read(config)['profile_sha256']:raise ValueError('演示配置指纹变化')
        return load_profile(path)
    return None

def main(argv=None):
    global RUN,ENV,ARGS
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['before','after','provision','tokens','acl','upload','status','publish','audit','preserve'])
    p.add_argument('--run-dir',type=Path,required=True,help='New private directory under tmp/')
    p.add_argument('--apply',action='store_true')
    p.add_argument('--allow-model-calls',action='store_true')
    p.add_argument('--review-note',default='')
    p.add_argument('--acknowledge-ocr',action='store_true')
    p.add_argument('--audit-name',default='index-audit.json',help='Use a new name to retain previous audit evidence')
    p.add_argument('--profile',type=Path,help='Validated interview/regression subset; original baseline unchanged')
    p.add_argument('--baseline-ref',default='8b6ed0c',help='Immutable Git base for original data/report preservation')
    ARGS=p.parse_args(argv);RUN=ARGS.run_dir.resolve()
    RUN.relative_to((ROOT/'tmp').resolve())
    if ARGS.command in ('provision','tokens','upload','publish') and not ARGS.apply:
        print(json.dumps(dict(dry_run=True,command=ARGS.command)));return 0
    if ARGS.command=='upload' and not ARGS.allow_model_calls:p.error('上传会触发嵌入调用，需要--allow-model-calls')
    RUN.mkdir(parents=True,exist_ok=True);ENV=dotenv_values(ROOT/'.env')
    if ARGS.command in ('before','after'):snapshot(ARGS.command)
    else:globals()[ARGS.command]()
    return 0

if __name__=='__main__':
    raise SystemExit(main())
