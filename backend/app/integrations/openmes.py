"""Bounded GET-only OpenMES adapter, with separate order/batch/step provenance."""
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import Field, SecretStr, ValidationError, model_validator

from ..manufacturing.contracts import Contract, Identifier, Text, WorkOrderRecord, canonical_digest, source_time
from ..manufacturing.batch_contracts import BatchChoice, StepChoice, BatchExecutionRecord
from .mes import MESError, MESNotFound, MESUnavailable, MESContextChanged, contains_credential


class OpenMESConfig(Contract):
    model_config=dict(extra='forbid',frozen=True,hide_input_in_errors=True)
    tenant_id: UUID
    source_system_id: Identifier
    base_url: str = Field(min_length=1,max_length=500)
    # Real upstream line IDs, configured by the operator, never supplied by a client.
    line_plants: dict[Identifier, Identifier] = Field(min_length=1,max_length=100)
    upstream_tenant_id: int = Field(ge=1,strict=True)
    api_token: SecretStr = Field(repr=False)
    read_token: SecretStr = Field(repr=False)

    @model_validator(mode='after')
    def endpoint(self):
        u=urlsplit(self.base_url)
        if (u.scheme not in ('http','https') or not u.hostname or u.username or u.password or
            u.path not in ('','/') or u.query or u.fragment or '\\' in self.base_url or any(c.isspace() for c in self.base_url)):
            raise ValueError('Invalid OpenMES source endpoint.')
        _=u.port
        if u.scheme=='http' and u.hostname not in ('backend','127.0.0.1','localhost','::1'):
            raise ValueError('Use the isolated OpenMES backend or HTTPS.')
        for secret in (self.api_token,self.read_token):
            value=secret.get_secret_value()
            if not 32<=len(value)<=512 or any(ord(c)<33 or ord(c)>126 for c in value):
                raise ValueError('Invalid OpenMES credential.')
        if any(not k.isdecimal() or str(int(k))!=k or int(k)<1 for k in self.line_plants):
            raise ValueError('Line IDs must be canonical positive integers.')
        return self


def upstream_id(value):
    if type(value) is int and value>0:return str(value)
    raise ValueError('Invalid upstream entity ID.')


def path_id(value):
    if not isinstance(value,str) or not value.isascii() or not value.isdecimal() or str(int(value))!=value or int(value)<1:
        raise MESNotFound()
    return value


def actual_start(raw):
    value=raw.get('started_at')
    if value is None:return dict(actual_started_at=None,actual_start_precision=None)
    source_time(value,'datetime')
    return dict(actual_started_at=value,actual_start_precision='datetime')


class OpenMESClient:
    def __init__(self,config: OpenMESConfig,*,transport=None):
        self.config=config
        self.client=httpx.AsyncClient(transport=transport or httpx.AsyncHTTPTransport(retries=0),trust_env=False,
            follow_redirects=False,timeout=httpx.Timeout(5,connect=2),headers={
                'Authorization':'Bearer '+config.api_token.get_secret_value(),'Accept':'application/json','Accept-Encoding':'identity'})

    async def aclose(self):await self.client.aclose()

    @property
    def configuration_sha256(self):
        return canonical_digest(self.config.model_dump(mode='json',exclude={'api_token','read_token'}))

    async def get(self,path):
        try:
            async with self.client.stream('GET',self.config.base_url.rstrip('/')+path) as response:
                if response.status_code in (403,404):raise MESNotFound()
                if response.status_code!=200:raise MESUnavailable()
                if response.headers.get('content-type','').split(';')[0]!='application/json' or response.headers.get('content-encoding','identity')!='identity':
                    raise MESError()
                raw=bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw)+len(chunk)>2_000_000:raise MESError()
                    raw.extend(chunk)
                from .mes import _reject_constant, _unique_object
                import json
                data=json.loads(raw,parse_constant=_reject_constant,object_pairs_hook=_unique_object)
                if not isinstance(data,dict) or 'data' not in data:raise MESError()
                return data['data']
        except (httpx.TimeoutException,httpx.TransportError):raise MESUnavailable() from None
        except (ValueError,RecursionError):raise MESError() from None

    def safe(self,value):
        raw=value.model_dump(mode='json')
        if any(contains_credential(raw,s.get_secret_value()) for s in (self.config.api_token,self.config.read_token)):
            raise MESError()
        return value

    def project_order(self,raw,plants):
        try:
            if not isinstance(raw,dict):raise ValueError('Missing order.')
            line=upstream_id(raw['line_id'])
            plant=self.config.line_plants.get(line)
            if plant not in plants or type(raw['tenant_id']) is not int or raw['tenant_id']!=self.config.upstream_tenant_id:raise MESNotFound()
            identifier=upstream_id(raw['id'])
            # Product code is the upstream product_type code, never a guessed name/SKU.
            product=raw.get('product_type')
            code=product.get('code') if isinstance(product,dict) else None
            projection=dict(source_system_id=self.config.source_system_id,tenant_id=str(self.config.tenant_id),
                plant_code=plant,work_order_id=identifier,source_record_id=identifier,product_model=code or None,
                operation_code=None,actual_started_at=None,actual_start_precision=None,status=raw['status'],
                source_updated_at=raw['updated_at'])
            # This OpenMES version has NO work-order actual-start field. Batch/step
            # started_at and planned_start_at must never fill that missing value.
            revision=canonical_digest(dict(configuration=self.configuration_sha256,order_no=raw['order_no'],**projection))
            return self.safe(WorkOrderRecord(**projection,source_revision='mes-'+revision[:60]))
        except (KeyError,ValueError,TypeError):raise MESError() from None

    async def orders(self,plants):
        raw=await self.get('/api/v1/work-orders')
        if not isinstance(raw,list) or len(raw)>1000:raise MESError()
        result=[]
        for item in raw:
            try:result.append(self.project_order(item,plants))
            except MESNotFound:continue
        if len({x.work_order_id for x in result})!=len(result):raise MESError()
        return sorted(result,key=lambda x:int(x.work_order_id))

    async def order(self,identifier,plants):
        record,_=await self.order_raw(identifier,plants)
        return record

    async def order_raw(self,identifier,plants):
        raw=await self.get('/api/v1/work-orders/'+path_id(identifier))
        record=self.project_order(raw,plants)
        if record.work_order_id!=identifier:raise MESError()
        return record,raw

    def project_batch(self,raw,order):
        try:
            if upstream_id(raw['work_order_id'])!=order.work_order_id:raise MESNotFound()
            return self.safe(BatchChoice(batch_id=upstream_id(raw['id']),work_order_id=order.work_order_id,
                batch_number=raw['batch_number'],lot_number=raw.get('lot_number'),status=raw['status'],
                source_updated_at=raw['updated_at'],**actual_start(raw)))
        except (KeyError,ValueError,TypeError):raise MESError() from None

    def project_steps(self,raw,batch):
        try:
            if not isinstance(raw,list) or len(raw)>100:raise MESError()
            result=[]
            for item in raw:
                if upstream_id(item['batch_id'])!=batch.batch_id:raise MESNotFound()
                result.append(self.safe(StepChoice(step_id=upstream_id(item['id']),batch_id=batch.batch_id,
                    work_order_id=batch.work_order_id,step_number=item['step_number'],name=item['name'],status=item['status'],
                    source_updated_at=item['updated_at'],**actual_start(item))))
            if len({x.step_id for x in result})!=len(result):raise MESError()
            return sorted(result,key=lambda x:(x.step_number,int(x.step_id)))
        except (KeyError,ValueError,TypeError):raise MESError() from None

    async def batches(self,identifier,plants):
        order=await self.order(identifier,plants)
        raw=await self.get('/api/v1/work-orders/'+identifier+'/batches')
        if not isinstance(raw,list) or len(raw)>100:raise MESError()
        rows=[self.project_batch(x,order) for x in raw]
        if len({x.batch_id for x in rows})!=len(rows):raise MESError()
        if (await self.order(identifier,plants))!=order:raise MESContextChanged()
        return order,rows

    async def selected_batch(self,order,batch_id):
        raw=await self.get('/api/v1/batches/'+path_id(batch_id))
        batch=self.project_batch(raw,order)
        if batch.batch_id!=batch_id:raise MESError()
        return batch,self.project_steps(raw.get('steps'),batch)

    async def execution(self,identifier,plants,*,batch_id=None,step_id=None):
        if step_id is not None and batch_id is None:raise MESNotFound()
        order,raw=await self.order_raw(identifier,plants)
        batch=step=None
        if batch_id is not None:
            batch,steps=await self.selected_batch(order,batch_id)
            if step_id is not None:
                step=next((x for x in steps if x.step_id==path_id(step_id)),None)
                if step is None:raise MESNotFound()
        if (await self.order(identifier,plants))!=order:raise MESContextChanged()
        data=dict(schema_version='openmes-execution-v1',work_order=order.model_dump(mode='json'),
            work_order_number=raw['order_no'],
            batch=batch.model_dump(mode='json') if batch else None,step=step.model_dump(mode='json') if step else None,
            configuration_sha256=self.configuration_sha256)
        return self.safe(BatchExecutionRecord(**data,source_revision='mes-'+canonical_digest(data)[:60]))
