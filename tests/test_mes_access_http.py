"""Real loopback HTTP plus database authorization; no external sources or models."""
import asyncio
from contextlib import contextmanager
from pathlib import Path
import socket
from threading import Thread
import time
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, delete, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
import uvicorn

from backend.app.db.models import Tenant, User
from backend.app.db.business_models import BusinessScopeGrant
from backend.app.integrations.mes import MESConnector, MESNotFound
from backend.app.integrations.mes_contracts import MESSourceConfig
from backend.app.manufacturing.access import WorkOrderAccessService
from backend.app.manufacturing.contracts import WorkOrderFixtures
from backend.app.security.business_scope import BusinessAuthorizationDenied
from backend.app.security.principal import Principal
from services.mock_mes.app import SourceCredential, create_app

ROOT=Path(__file__).resolve().parents[1]
TOKEN="m1-test-loopback-credential-not-production-0003"


@pytest.fixture
def context_data():
    fixtures=WorkOrderFixtures.model_validate_json((ROOT/"sample_docs/manufacturing_workorder_v1/work_orders.json").read_bytes())
    engine=create_engine("sqlite+pysqlite:///:memory:",poolclass=StaticPool,connect_args={"check_same_thread":False})
    @event.listens_for(engine,"connect")
    def foreign_keys(connection,_): connection.execute("PRAGMA foreign_keys=ON")
    for model in (Tenant,User,BusinessScopeGrant): model.__table__.create(engine)
    principal=Principal(uuid4(),fixtures.tenant_id,"http-owner")
    with Session(engine) as session:
        session.add(Tenant(id=fixtures.tenant_id,name="M1 source"));session.flush()
        session.add(User(id=principal.user_id,tenant_id=principal.tenant_id,external_subject=principal.external_subject,name="owner"));session.flush()
        session.add(BusinessScopeGrant(tenant_id=principal.tenant_id,user_id=principal.user_id,
            source_system_id=fixtures.source_system_id,plant_code="HC-P01",permission="view"));session.commit()
        yield fixtures,session,principal
    engine.dispose()


@contextmanager
def serving(fixtures):
    app=create_app(fixtures,(SourceCredential(fixtures.tenant_id,fixtures.source_system_id,
        frozenset({"HC-P01","HC-P02"}),SecretStr(TOKEN)),))
    sock=socket.socket();sock.bind(("127.0.0.1",0));sock.listen(32)
    port=sock.getsockname()[1]
    # Selector loops avoid Windows Proactor's reset-connection shutdown race when
    # the bounded client intentionally closes an unread upstream error body.
    server=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=port,log_level="warning",access_log=False,
        lifespan="off",loop="asyncio:SelectorEventLoop",timeout_graceful_shutdown=1,timeout_keep_alive=1))
    thread=Thread(target=server.run,kwargs={"sockets":[sock]},daemon=True);thread.start()
    try:
        deadline=time.monotonic()+5
        while not server.started and thread.is_alive() and time.monotonic()<deadline: time.sleep(.01)
        assert server.started,"loopback service failed to start"
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit=True;thread.join(5);sock.close()
        assert not thread.is_alive(),"loopback service did not stop"


@pytest.mark.integration
def test_real_http_query_uses_db_grants_and_source_checks(context_data):
    fixtures,session,principal=context_data
    with serving(fixtures) as base:
        async def scenario():
            config=MESSourceConfig(source_system_id=fixtures.source_system_id,tenant_id=fixtures.tenant_id,
                base_url=base,allowed_plants=("HC-P01","HC-P02"),token=SecretStr(TOKEN))
            async with MESConnector(config) as connector:
                service=WorkOrderAccessService(session,connector)
                page=await service.list_work_orders(principal=principal)
                assert len(page.items)==7 and "WO-022" not in {r.work_order_id for r in page.items}
                result=await service.read_context(principal=principal,work_order_id="WO-017")
                assert result.record.product_model=="HC-A17"
                with pytest.raises(MESNotFound): await service.read_context(principal=principal,work_order_id="WO-022")
                session.execute(delete(BusinessScopeGrant));session.commit()
                with pytest.raises(BusinessAuthorizationDenied): await service.read_context(principal=principal,work_order_id="WO-017")
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(scenario())


@pytest.mark.parametrize("source_failed", [False,True])
def test_permission_revoked_during_source_read_is_checked_before_return(context_data,source_failed):
    fixtures,session,principal=context_data
    calls=[]
    def source(request):
        calls.append(request)
        session.execute(delete(BusinessScopeGrant));session.commit()
        return httpx.Response(503) if source_failed else httpx.Response(200,json=fixtures.records[0].model_dump(mode="json"))
    async def scenario():
        config=MESSourceConfig(source_system_id=fixtures.source_system_id,tenant_id=fixtures.tenant_id,
            base_url="http://mock-mes",allowed_plants=("HC-P01",),token=SecretStr(TOKEN))
        async with MESConnector(config,transport=httpx.MockTransport(source)) as connector:
            with pytest.raises(BusinessAuthorizationDenied):
                await WorkOrderAccessService(session,connector).read_context(principal=principal,work_order_id="WO-017")
    asyncio.run(scenario());assert len(calls)==1
