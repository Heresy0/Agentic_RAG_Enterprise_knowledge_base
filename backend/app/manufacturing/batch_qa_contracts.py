"""OpenMES QA contract, separate from the frozen ERP execution schema."""
from typing import Literal

from pydantic import model_validator

from .contracts import WorkOrderRecord, WorkOrderContext, Text, Digest, canonical_digest
from .batch_contracts import BatchChoice, StepChoice

SCHEMA = 'openmes-work-order-v1'


class BatchWorkOrderRecord(WorkOrderRecord):
    schema_version: Literal['openmes-work-order-v1'] = SCHEMA
    work_order_number: Text
    batch: BatchChoice | None = None
    step: StepChoice | None = None
    configuration_sha256: Digest

    @property
    def batch_id(self):
        return self.batch.batch_id if self.batch is not None else None

    @property
    def step_id(self):
        return self.step.step_id if self.step is not None else None

    @model_validator(mode='after')
    def bound_source(self):
        if self.batch is not None and self.batch.work_order_id != self.work_order_id:
            raise ValueError('批次不属于所选工单。')
        if self.step is not None and (self.batch is None or
                (self.step.work_order_id,self.step.batch_id)!=(self.work_order_id,self.batch_id)):
            raise ValueError('步骤不属于所选工单/批次。')
        if self.actual_started_at is not None or self.operation_code is not None:
            raise ValueError('当前OpenMES来源没有工单实际开工或唯一工序代码。')
        if self.source_revision != 'mes-'+canonical_digest(self.model_dump(mode='json',exclude={'source_revision'}))[:60]:
            raise ValueError('问答来源修订不一致。')
        return self


class BatchWorkOrderContext(WorkOrderContext):
    record: BatchWorkOrderRecord


def qa_record(execution):
    values=execution.work_order.model_dump(mode='json',exclude={'source_revision'})
    values.update(schema_version=SCHEMA,work_order_number=execution.work_order_number,
        batch=execution.batch.model_dump(mode='json') if execution.batch else None,
        step=execution.step.model_dump(mode='json') if execution.step else None,
        configuration_sha256=execution.configuration_sha256)
    return BatchWorkOrderRecord(**values,source_revision='mes-'+canonical_digest(values)[:60])
