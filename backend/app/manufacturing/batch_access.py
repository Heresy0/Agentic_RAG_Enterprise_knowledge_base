"""Authorized deterministic execution reads; no model, writes, or conversation rebinding."""
from datetime import datetime, timezone
from urllib.parse import quote

from pydantic import ValidationError

from .access import WorkOrderAccessService
from .batch_contracts import BatchPage, StepPage, BatchExecutionRecord, BatchExecutionContext
from ..integrations.mes import MESError, MESSelectionUnavailable, MESContextChanged, contains_credential


class BatchExecutionAccess(WorkOrderAccessService):
    def require_capability(self):
        if not self.connector.config.batch_step_selection:raise MESSelectionUnavailable()

    async def read(self,principal,path,model):
        self.require_capability()
        scope=self.scope(principal)
        try:
            raw,plants=await self.connector._get(path,scope=scope)
            try:value=model.model_validate(raw)
            except ValidationError:raise MESError() from None
            if contains_credential(value.model_dump(mode='json'),self.connector.config.token.get_secret_value()):raise MESError()
            if isinstance(value,BatchExecutionRecord):
                record=value.work_order
                if not scope.contains_record(record) or record.plant_code not in plants:raise MESError()
            return value
        finally:self.authorization.require_current(principal=principal,scope=scope)

    async def batches(self,principal,work_order_id):
        value=await self.read(principal,f'/v1/work-orders/{quote(work_order_id,safe="")}/batches',BatchPage)
        if value.work_order_id!=work_order_id:raise MESError()
        return value

    async def steps(self,principal,work_order_id,batch_id):
        value=await self.read(principal,f'/v1/work-orders/{quote(work_order_id,safe="")}/batches/{quote(batch_id,safe="")}/steps',StepPage)
        if (value.work_order_id,value.batch_id)!=(work_order_id,batch_id):raise MESError()
        return value

    async def context(self,principal,work_order_id,*,batch_id=None,step_id=None,expected_source_revision=None):
        from urllib.parse import urlencode
        params=urlencode({k:v for k,v in dict(batch_id=batch_id,step_id=step_id).items() if v is not None})
        path=f'/v1/work-orders/{quote(work_order_id,safe="")}/execution-context'+('?' +params if params else '')
        first=await self.read(principal,path,BatchExecutionRecord)
        if (first.work_order.work_order_id,first.batch.batch_id if first.batch else None,first.step.step_id if first.step else None)!=(work_order_id,batch_id,step_id):
            raise MESError()
        if expected_source_revision is not None and first.source_revision!=expected_source_revision:raise MESContextChanged()
        fetched=datetime.now(timezone.utc)
        current=await self.read(principal,path,BatchExecutionRecord)
        if current!=first:raise MESContextChanged()
        return BatchExecutionContext(record=current,fetched_at=fetched,last_verified_at=datetime.now(timezone.utc))


def public_execution(context):
    value=context.model_dump(mode='json')
    record=value['record'];record['work_order'].pop('tenant_id')
    citations=[]
    for entity,row,identity in [('Work Order',record['work_order'],'source_record_id'),('Batch',record['batch'],'batch_id'),('Batch Step',record['step'],'step_id')]:
        if row is None:continue
        keys={'Work Order':('work_order_id','product_model'), 'Batch':('batch_id','batch_number','lot_number'),
              'Batch Step':('step_id','step_number','name')}[entity]
        for key in (*keys,'status','actual_started_at','actual_start_precision','source_updated_at'):
            citations.append(dict(source_entity=entity,source_record_id=row[identity],field_name=key,value=row[key],
                source_system_id=record['work_order']['source_system_id'],source_revision=record['source_revision'],
                work_order_id=record['work_order']['work_order_id'],batch_id=record['batch']['batch_id'] if record['batch'] else None,
                step_id=record['step']['step_id'] if record['step'] else None))
    return dict(**value,business_citations=citations)
