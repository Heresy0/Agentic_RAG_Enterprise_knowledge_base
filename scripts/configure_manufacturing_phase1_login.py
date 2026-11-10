"""Add a separate PKCE client for the M4 instance; preserve the existing web client."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

from dotenv import dotenv_values
import requests

ROOT=Path(__file__).resolve().parents[1]
CLIENT_ID='enterprise-knowledge-phase1-web'


def public_client(source,port):
    if port in (8000,8001) or not 1024<=port<=65535 or not source.get('publicClient'):raise ValueError('仅允许独立端口和公共 PKCE 客户端。')
    origin='http://127.0.0.1:'+str(port)
    mappers=[{key:deepcopy(value) for key,value in row.items() if key!='id'} for row in source.get('protocolMappers',[])]
    if not any(row.get('protocolMapper')=='oidc-audience-mapper' and row.get('config',{}).get('included.client.audience')=='enterprise-knowledge-api' for row in mappers):
        raise ValueError('缺少 API audience 映射，不复制不明配置。')
    return dict(clientId=CLIENT_ID,name='首期工单集成 M4 浏览器',enabled=True,publicClient=True,protocol='openid-connect',
        standardFlowEnabled=True,implicitFlowEnabled=False,directAccessGrantsEnabled=False,serviceAccountsEnabled=False,
        redirectUris=[origin+'/'],webOrigins=[origin],defaultClientScopes=list(source.get('defaultClientScopes',[])),
        attributes={'pkce.code.challenge.method':'S256','post.logout.redirect.uris':origin+'/'},protocolMappers=mappers)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-dir',type=Path,required=True);parser.add_argument('--apply',action='store_true')
    args=parser.parse_args(argv);run=args.run_dir.resolve();run.relative_to(ROOT/'tmp')
    state=json.loads((run/'state.json').read_text(encoding='utf-8'));port=state['port']
    if state['database']!='manufacturing_phase1_m4' or port in (8000,8001):raise ValueError('不是独立 M4 实例。')
    if not args.apply:print(json.dumps(dict(mode='dry_run',client_id=CLIENT_ID,port=port,old_client_modified=False)));return 0
    if (run/'login-client.json').exists() or (run/'login.env').exists():raise ValueError('已有登录配置，拒绝覆盖。')
    cfg=dotenv_values(ROOT/'.env');http=requests.Session()
    result=http.post('http://127.0.0.1:8080/realms/master/protocol/openid-connect/token',data=dict(grant_type='password',client_id='admin-cli',
        username=cfg['KEYCLOAK_ADMIN_USERNAME'],password=cfg['KEYCLOAK_ADMIN_PASSWORD']),timeout=15)
    if result.status_code!=200:raise ValueError('管理身份认证失败。')
    http.headers['Authorization']='Bearer '+result.json()['access_token'];base='http://127.0.0.1:8080/admin/realms/enterprise-knowledge/clients'
    old=http.get(base,params=dict(clientId='enterprise-knowledge-web'),timeout=15);old.raise_for_status()
    if len(old.json())!=1:raise ValueError('原浏览器客户端不唯一。')
    config=public_client(old.json()[0],port)
    existing=http.get(base,params=dict(clientId=CLIENT_ID),timeout=15);existing.raise_for_status()
    if existing.json():raise ValueError('新客户端已存在，拒绝更改既有配置。')
    created=http.post(base,json=config,timeout=15)
    if created.status_code!=201:raise ValueError('独立客户端创建失败。')
    with (run/'login-client.json').open('x',encoding='utf-8') as stream:json.dump(dict(config=config,old_client_modified=False),stream,ensure_ascii=False,indent=2)
    with (run/'login.env').open('x',encoding='utf-8') as stream:stream.write('OIDC_FRONTEND_CLIENT_ID='+CLIENT_ID+'\n')
    print(json.dumps(dict(mode='created',client_id=CLIENT_ID,port=port,old_client_modified=False)));return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        print(json.dumps(dict(status='failed',error_type=type(exc).__name__)),file=sys.stderr);raise SystemExit(1) from None
