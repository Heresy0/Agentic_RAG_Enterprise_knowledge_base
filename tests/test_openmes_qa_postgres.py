"""OpenMES binding and migration checks in the existing isolated M3 database."""
import asyncio
from contextlib import contextmanager
import os
import subprocess
import sys
from uuid import UUID

import httpx
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select,text
from sqlalchemy.exc import IntegrityError

from test_manufacturing_m3_postgres import pg_context
from test_openmes_adapter import Graph,config,TOKEN
from test_openmes_qa import request,conversation
from backend.app.db.business_models import ConversationBusinessBinding
from backend.app.integrations.mes import MESConnector
from backend.app.integrations.mes_contracts import MESSourceConfig
from services.openmes_adapter.app import create_app

pytestmark=pytest.mark.integration


def test_postgres_batch_binding_constraints_snapshot_and_guarded_downgrade(monkeypatch):
    with contextmanager(pg_context)(monkeypatch) as h:
        graph=Graph()
        app=create_app(config(tenant_id=h.c.tenant_id,source_system_id='mock-mes-hc'),transport=httpx.MockTransport(graph))
        connector=MESConnector(MESSourceConfig(tenant_id=h.c.tenant_id,source_system_id='mock-mes-hc',base_url='http://openmes-adapter:8012',
            token=TOKEN,allowed_plants=('HC-P01',),batch_step_selection=True),transport=httpx.ASGITransport(app))
        h.c.app.state.mes_connectors={h.c.tenant_id:connector}
        try:
            with TestClient(h.c.app) as c:
                identifier=conversation(c,h)
                resp=c.post('/api/manufacturing/qa',json=request(h,conversation_id=identifier))
                assert resp.status_code==200,resp.text
                run=c.get('/api/qa/runs/'+resp.json()['run_id'])
                assert run.status_code==200 and run.json()['snapshot']['manufacturing']['validation']['mes_reads']==3
            with h.c.session_factory() as db:
                binding=db.scalar(select(ConversationBusinessBinding).where(ConversationBusinessBinding.conversation_id==UUID(identifier)))
                assert (binding.batch_id,binding.step_id)==('1','11')
            for values in ('batch_id=NULL','operation_id=\'ROW-A\''):
                with h.c.session_factory() as db:
                    with pytest.raises(IntegrityError):
                        db.execute(text('UPDATE conversation_business_bindings SET '+values+' WHERE conversation_id=:id'),dict(id=identifier));db.commit()
                    db.rollback()
            env=dict(os.environ,DATABASE_URL=os.environ['MANUFACTURING_TEST_DATABASE_URL'])
            result=subprocess.run([sys.executable,'-X','utf8','-m','alembic','downgrade','f2b8c0a961d3'],env=env,capture_output=True)
            guarded=result.returncode!=0 and '丢失业务边界'.encode('utf-8') in result.stderr
            assert guarded,'OpenMES binding downgrade was not guarded.'
            with h.c.session_factory() as db:
                assert db.scalar(text('SELECT version_num FROM alembic_version'))=='e9b2d4f60713'
        finally:
            asyncio.run(connector.aclose());asyncio.run(app.state.openmes_client.aclose())


def test_postgres_step_mapping_keeps_history_and_blocks_destructive_downgrade(monkeypatch):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import importlib
    from backend.app.db.models import KnowledgeBaseUserGrant
    from backend.app.db.business_models import BusinessScopeGrant,StepGuidanceRevision
    from test_openmes_guidance import mapping,path
    with contextmanager(pg_context)(monkeypatch) as h:
        with h.c.session_factory() as db:
            db.scalar(select(KnowledgeBaseUserGrant).where(KnowledgeBaseUserGrant.user_id==h.c.editor.user_id)).permission='admin'
            db.add(BusinessScopeGrant(tenant_id=h.c.tenant_id,user_id=h.c.editor.user_id,source_system_id='mock-mes-hc',plant_code='HC-P01',permission='review'));db.commit()
        app=create_app(config(tenant_id=h.c.tenant_id,source_system_id='mock-mes-hc'),transport=httpx.MockTransport(Graph()))
        connector=MESConnector(MESSourceConfig(tenant_id=h.c.tenant_id,source_system_id='mock-mes-hc',base_url='http://openmes-adapter:8012',token=TOKEN,
            allowed_plants=('HC-P01',),batch_step_selection=True),transport=httpx.ASGITransport(app))
        h.c.app.state.mes_connectors={h.c.tenant_id:connector}
        try:
            with TestClient(h.c.app) as c:
                mapping(c,h)
                assert c.put(path(h),json=dict(expected_revision=2,state='withdrawn',review_note='保留历史的撤回')).status_code==200
                history=c.get(path(h)).json()['history'];assert [x['revision'] for x in history]==[3,2,1]
            migration=importlib.import_module('migrations.versions.e9b2d4f60713_openmes_step_guidance')
            with h.engine.begin() as connection,Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError,match='拒绝降级丢失审核记录'):migration.downgrade()
                assert connection.scalar(text('SELECT count(*) FROM step_guidance_revisions'))==3
        finally:asyncio.run(connector.aclose());asyncio.run(app.state.openmes_client.aclose())
