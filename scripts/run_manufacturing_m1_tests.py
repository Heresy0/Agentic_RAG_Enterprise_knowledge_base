"""M1 PostgreSQL checks in an explicit independent database; never migrates the demo DB."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT=Path(__file__).resolve().parents[1]
TEST_NAME=r"manufacturing_m1_test_[0-9a-f]{12}"


def checked_test_url(value):
    url=make_url(value)
    if (url.get_backend_name()!="postgresql" or not re.fullmatch(TEST_NAME,url.database or "") or
            url.host not in ("127.0.0.1","localhost","::1")):
        raise ValueError("只允许明确命名的本机独立M1测试数据库。")
    return url


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create-test-database",action="store_true")
    parser.add_argument("--output-dir",type=Path,default=ROOT/"tmp/manufacturing-phase1-m1/postgres-r1")
    args=parser.parse_args(argv)
    output=args.output_dir.resolve()
    if not output.is_relative_to((ROOT/"tmp").resolve()): raise ValueError("测试产物必须位于仓库tmp目录。")
    report=output/"database-verification.json";junit=output/"postgres-tests.xml"
    if report.exists() or junit.exists(): raise ValueError("已有测试产物，须使用新的输出目录。")
    admin=None;created=False;removed=False;result={"status":"failed","model_calls":0};exit_code=1
    if args.create_test_database:
        # Read only the necessary local configuration. Never print or persist the URL.
        configured=os.environ.get("MANUFACTURING_ADMIN_DATABASE_URL") or dotenv_values(ROOT/".env").get("DATABASE_URL")
        if not configured: raise ValueError("未配置本机PostgreSQL管理连接。")
        original=make_url(configured)
        if original.host not in ("127.0.0.1","localhost","::1","postgres"):
            raise ValueError("独立测试数据库必须在本机。")
        admin_url=original.set(host="127.0.0.1",database="postgres")
        name="manufacturing_m1_test_"+uuid4().hex[:12]
        url=checked_test_url(admin_url.set(database=name))
        admin=create_engine(admin_url,isolation_level="AUTOCOMMIT",connect_args={"connect_timeout":5})
    else:
        value=os.environ.get("MANUFACTURING_TEST_DATABASE_URL")
        if not value: raise ValueError("请提供独立测试数据库或显式指定--create-test-database。")
        url=checked_test_url(value);name=url.database
    secret_url=url.render_as_string(hide_password=False)
    env=dict(os.environ,DATABASE_URL=secret_url,MANUFACTURING_TEST_DATABASE_URL=secret_url,
        RUN_QA_FEEDBACK_INTEGRATION_TESTS="1",PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",PYTHONIOENCODING="utf-8")
    output.mkdir(parents=True,exist_ok=True)
    try:
        if admin is not None:
            assert re.fullmatch(TEST_NAME,name)
            with admin.connect() as connection: connection.execute(text(f'CREATE DATABASE "{name}"'))
            created=True
        migration=subprocess.run([sys.executable,"-m","alembic","upgrade","head"],cwd=ROOT,env=env,
                                 stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace")
        log=migration.stdout.replace(secret_url,"[redacted database URL]")
        if url.password: log=log.replace(url.password,"[redacted]")
        (output/"migration.log").write_text(log,encoding="utf-8")
        if migration.returncode: raise RuntimeError("独立测试数据库完整迁移失败，详情见脱敏日志。")
        engine=create_engine(url)
        try:
            with engine.connect() as connection:
                head=connection.scalar(text("SELECT version_num FROM alembic_version"))
                version=connection.scalar(text("SHOW server_version_num"))
        finally: engine.dispose()
        if head!="d8f0b2c43569": raise RuntimeError("独立测试数据库迁移头不符。")
        tests=subprocess.run([sys.executable,"-m","pytest","tests/test_business_migration_postgres.py",
            "tests/test_quality_verification_postgres.py","-q","-o","addopts=",
            f"--junitxml={junit}"],cwd=ROOT,env=env)
        exit_code=tests.returncode
        result=dict(status="passed" if exit_code==0 else "failed",database=name,head=head,
                    server_version_num=version,tests_exit_code=exit_code,model_calls=0)
    finally:
        try:
            if created:
                assert admin is not None and re.fullmatch(TEST_NAME,name)
                with admin.connect() as connection:
                    connection.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name AND pid<>pg_backend_pid()"),{"name":name})
                    connection.execute(text(f'DROP DATABASE "{name}"'))
                removed=True
        finally:
            if admin is not None: admin.dispose()
            result.update(database_created=created,database_removed=removed,business_database_modified=False)
            if created and not removed: result.update(status="failed",cleanup_error=True)
            report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=True))
    return exit_code


if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        print(json.dumps(dict(status="failed",error_type=type(exc).__name__)),file=sys.stderr)
        raise SystemExit(1) from None
