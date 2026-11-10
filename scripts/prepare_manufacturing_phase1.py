"""Prepare an independent M4 database/vector namespace and private Compose file.

Dry-run by default. Never alters 8000/8001 containers, old roles or existing DBs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from uuid import uuid4

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.manufacturing.contracts import WorkOrderFixtures, canonical_digest
from backend.app.config import read_milvus_token_file
from scripts.bootstrap_milvus import authenticate_admin, configure_services, READ_PRIVILEGES, WRITE_PRIVILEGES

DATABASE = "manufacturing_phase1_m4"
COLLECTION = "manufacturing_phase1_workorders"
ACCOUNTS = {"api": ("mfg_phase1_api", "mfg_phase1_read", READ_PRIVILEGES),
            "worker": ("mfg_phase1_worker", "mfg_phase1_write", WRITE_PRIVILEGES)}


def private_write(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)+"\n")


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--credentials",type=Path,required=True,help="Existing Keycloak subjects; never prints passwords")
    parser.add_argument("--image",default="enterprise-knowledge-manufacturing:phase1-m4-20261101-r1")
    parser.add_argument("--port",type=int,default=8002)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args(argv)
    output=args.output_dir.resolve()
    if not output.is_relative_to(ROOT/"tmp") or args.port in (8000,8001) or not 1024<=args.port<=65535:
        raise ValueError("须使用仓库 tmp 中的新目录和独立端口。")
    credentials=json.loads(args.credentials.read_text(encoding="utf-8-sig"))
    subjects={role:row["subject"] for role,row in credentials.items()}
    if len(set(subjects.values()))!=len(subjects) or "知识管理员" not in subjects:
        raise ValueError("演示身份不完整或重复。")
    fixtures=WorkOrderFixtures.model_validate_json((ROOT/"sample_docs/manufacturing_workorder_v1/work_orders.json").read_bytes())
    manifest=json.loads((ROOT/"sample_docs/manufacturing_workorder_v1/manifest.json").read_text(encoding="utf-8"))
    groups=sorted({row["group"] for row in manifest["documents"]})
    planned=dict(database=DATABASE,collection=COLLECTION,tenant_id=str(fixtures.tenant_id),port=args.port,
        image=args.image,documents=len(manifest["documents"]),groups=groups,subjects=len(subjects),model_calls=0,
        stable_instances_modified=False)
    if not args.apply:
        print(json.dumps(dict(mode="dry_run",**planned),ensure_ascii=True));return 0
    if output.exists(): raise ValueError("已有输出目录，拒绝覆盖部署配置或证据。")
    configured=dotenv_values(ROOT/".env")
    original=make_url(configured["DATABASE_URL"])
    if original.host not in ("127.0.0.1","localhost","postgres"): raise ValueError("只允许本机数据库。")
    admin=create_engine(original.set(host="127.0.0.1",database="postgres"),isolation_level="AUTOCOMMIT",connect_args={"connect_timeout":5})
    try:
        with admin.connect() as connection:
            if connection.scalar(text("SELECT 1 FROM pg_database WHERE datname=:name"),{"name":DATABASE}):
                raise ValueError("独立数据库已存在，拒绝复用或覆盖。")
        vector_admin=authenticate_admin("http://127.0.0.1:19531",COLLECTION,
            read_milvus_token_file(str(ROOT/".secrets/milvus/admin_token")))
        if vector_admin._call("collections/has",collectionName=COLLECTION).get("has"):
            raise ValueError("独立向量集合已存在，拒绝覆盖。")
        output.mkdir(parents=True);(output/"config").mkdir();(output/"secrets").mkdir()
        source_token=secrets.token_urlsafe(40)
        tokens={key:account[0]+":"+secrets.token_urlsafe(40) for key,account in ACCOUNTS.items()}
        for purpose,token in tokens.items(): private_write(output/"secrets"/(purpose+".token"),token+"\n")
        source=dict(schema_version="mes-sources-v1",sources=[dict(tenant_id=str(fixtures.tenant_id),
            source_system_id=fixtures.source_system_id,base_url="http://mock-mes:8010",allowed_plants=["HC-P01","HC-P02"],token_env="MOCK_MES_READ_TOKEN")])
        private_write(output/"config/sources.json",source)
        private_write(output/"config/registry.json",dict(schema_version="manufacturing-evaluation-registry-v1",active=[dict(
            tenant_id=str(fixtures.tenant_id),source_system_id=fixtures.source_system_id,fixture_version=fixtures.fixture_version,
            fixture_sha256=canonical_digest(fixtures.model_dump(mode="json")))]))
        container_url=original.set(host="postgres",database=DATABASE).render_as_string(hide_password=False)
        local_url=original.set(host="127.0.0.1",database=DATABASE).render_as_string(hide_password=False)
        env=dict(DATABASE_URL=container_url,REDIS_URL="redis://m4-redis:6379/0",MILVUS_COLLECTION=COLLECTION,
            MILVUS_URI="http://milvus:19530",MILVUS_TOKEN="",MILVUS_TOKEN_FILE="/run/m4-credential/token",
            MILVUS_WORKER_TOKEN_FILE="/run/m4-credential/token",MANUFACTURING_SOURCES_FILE="/app/config/m4/sources.json",
            MANUFACTURING_EVALUATION_REGISTRY="/app/config/m4/registry.json",MOCK_MES_READ_TOKEN=source_token,
            OIDC_JWKS_URL="http://keycloak:8080/realms/enterprise-knowledge/protocol/openid-connect/certs",
            DOCUMENT_STORAGE_DIR="/app/data/documents",QA_SNAPSHOTS_ENABLED="true")
        private_write(output/"runtime.env","".join(key+"="+value+"\n" for key,value in env.items()))
        private_write(output/"mock.env","MOCK_MES_READ_TOKEN="+source_token+"\n")
        # Config and source mounts contain no standard answers. Acceptance gets
        # an additional read-only benchmark mount only when explicitly run.
        config_mount=str(output/"config")+":/app/config/m4:ro"
        document_volume="phase1_documents:/app/data/documents"
        def service(purpose):
            return dict(image=args.image,env_file=[str(ROOT/".env"),str(output/"runtime.env")],
                volumes=[config_mount,document_volume,str(output/"secrets"/(purpose+".token"))+":/run/m4-credential/token:ro"],
                networks=["shared","phase1"],restart="no")
        api=service("api");api.update(container_name="manufacturing-phase1-api",ports=[f"127.0.0.1:{args.port}:8000"])
        worker=service("worker");worker.update(container_name="manufacturing-phase1-indexer",command=["python","scripts/run_indexing_worker.py"],healthcheck=dict(disable=True))
        quality=service("api");quality.update(container_name="manufacturing-phase1-quality",profiles=["quality"],
            command=["python","scripts/run_quality_worker.py","--allow-model-calls","--once"],healthcheck=dict(disable=True))
        mock=dict(image=args.image,container_name="manufacturing-phase1-mes",env_file=[str(output/"mock.env")],
            environment=dict(MANUFACTURING_SOURCES_FILE="/app/config/m4/sources.json",MOCK_MES_FIXTURES_FILE="/app/fixtures/work_orders.json"),
            command=["python","-m","uvicorn","services.mock_mes.main:app","--factory","--host","0.0.0.0","--port","8010","--no-access-log"],
            volumes=[config_mount,str(ROOT/"sample_docs/manufacturing_workorder_v1/work_orders.json")+":/app/fixtures/work_orders.json:ro"],
            networks={"phase1":{"aliases":["mock-mes"]}},healthcheck=dict(disable=True),restart="no")
        redis=dict(image="redis:8.2.10-alpine",container_name="manufacturing-phase1-redis",networks={"phase1":{"aliases":["m4-redis"]}},
            command=["redis-server","--save","","--appendonly","no"],healthcheck=dict(disable=True),restart="no")
        compose=dict(name="manufacturing-phase1",services=dict(api=api,worker=worker,quality=quality,mock_mes=mock,redis=redis),
            networks=dict(shared=dict(external=True,name="enterprise_knowladge_default"),phase1={}),volumes=dict(phase1_documents={}))
        private_write(output/"compose.json",compose)
        with admin.connect() as connection: connection.execute(text('CREATE DATABASE "'+DATABASE+'"'))
        migration_env=dict(os.environ,DATABASE_URL=local_url,PYTHONIOENCODING="utf-8")
        migration=subprocess.run([sys.executable,"-m","alembic","upgrade","head"],cwd=ROOT,env=migration_env,
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace")
        log=migration.stdout.replace(local_url,"[database URL]")
        if original.password: log=log.replace(original.password,"[redacted]")
        private_write(output/"migration.log",log)
        if migration.returncode: raise RuntimeError("独立候选迁移失败，保留新库及脱敏日志。")
        engine=create_engine(local_url)
        try:
            from backend.app.db.models import Tenant,User,KnowledgeBase,KnowledgeBaseUserGrant
            from backend.app.db.business_models import BusinessScopeGrant
            from backend.app.db.session import create_session_factory
            with create_session_factory(engine)() as session:
                tenant=Tenant(id=fixtures.tenant_id,name="首期工单集成 M4 独立验收");session.add(tenant);session.flush()
                bases={name:KnowledgeBase(tenant_id=tenant.id,name=name,visibility="company" if name=="公共参考" else "restricted") for name in groups}
                session.add_all(bases.values());session.flush()
                users={}
                for role,subject in subjects.items():
                    user=User(tenant_id=tenant.id,external_subject=subject,name=role);session.add(user);session.flush();users[role]=str(user.id)
                    for group,kb in bases.items():
                        permission="admin" if role=="知识管理员" else "editor" if role=="工艺工程师" and group=="生产工艺" else "viewer" if (
                            role=="质量工程师" and group=="质量检验" or role=="操作员" and group=="生产工艺") else None
                        if permission:session.add(KnowledgeBaseUserGrant(tenant_id=tenant.id,user_id=user.id,knowledge_base_id=kb.id,permission=permission))
                    if role in ("知识管理员","工艺工程师","质量工程师","操作员"):
                        session.add(BusinessScopeGrant(tenant_id=tenant.id,user_id=user.id,source_system_id=fixtures.source_system_id,plant_code="HC-P01",permission="view"))
                    if role=="知识管理员":session.add(BusinessScopeGrant(tenant_id=tenant.id,user_id=user.id,source_system_id=fixtures.source_system_id,plant_code="HC-P01",permission="review"))
                session.commit()
                state=dict(**planned,knowledge_bases={name:str(row.id) for name,row in bases.items()},users=users,
                    database_migrated=True,fixture_version=fixtures.fixture_version,fixture_sha256=canonical_digest(fixtures.model_dump(mode="json")))
        finally: engine.dispose()
        vector_admin.ensure_collection()
        configure_services(vector_admin,tokens,accounts=ACCOUNTS)
        private_write(output/"state.json",state)
        print(json.dumps(dict(mode="prepared",**planned),ensure_ascii=True));return 0
    finally:admin.dispose()


if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        print(json.dumps(dict(status="failed",error_type=type(exc).__name__)),file=sys.stderr)
        raise SystemExit(1) from None
