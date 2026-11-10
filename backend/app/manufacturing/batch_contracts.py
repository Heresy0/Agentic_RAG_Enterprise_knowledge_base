"""OpenMES execution views. Batches and steps never impersonate ERP job cards."""
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .contracts import Contract, Identifier, Text, Digest, WorkOrderRecord, canonical_digest, aware_source_timestamp
from .execution_contracts import ActualStart


class BatchChoice(ActualStart):
    batch_id: Identifier
    work_order_id: Identifier
    batch_number: int = Field(ge=1, strict=True)
    lot_number: Text | None
    status: Text
    source_updated_at: AwareDatetime

    _source_time = field_validator('source_updated_at',mode='before')(aware_source_timestamp)


class StepChoice(ActualStart):
    step_id: Identifier
    batch_id: Identifier
    work_order_id: Identifier
    step_number: int = Field(ge=1, strict=True)
    name: Text
    status: Text
    source_updated_at: AwareDatetime

    _source_time = field_validator('source_updated_at',mode='before')(aware_source_timestamp)


class BatchPage(Contract):
    work_order_id: Identifier
    source_revision: Identifier
    items: tuple[BatchChoice, ...] = Field(max_length=100)

    @model_validator(mode='after')
    def bound_items(self):
        if len({x.batch_id for x in self.items}) != len(self.items) or any(x.work_order_id != self.work_order_id for x in self.items):
            raise ValueError('批次不属于当前工单或身份重复。')
        return self


class StepPage(Contract):
    work_order_id: Identifier
    batch_id: Identifier
    source_revision: Identifier
    items: tuple[StepChoice, ...] = Field(max_length=100)

    @model_validator(mode='after')
    def bound_items(self):
        if len({x.step_id for x in self.items}) != len(self.items) or any(
                (x.work_order_id,x.batch_id)!=(self.work_order_id,self.batch_id) for x in self.items):
            raise ValueError('步骤不属于当前工单/批次或身份重复。')
        return self


class BatchExecutionRecord(Contract):
    schema_version: Literal['openmes-execution-v1'] = 'openmes-execution-v1'
    work_order: WorkOrderRecord
    work_order_number: Text
    batch: BatchChoice | None = None
    step: StepChoice | None = None
    source_revision: Identifier
    configuration_sha256: Digest

    @model_validator(mode='after')
    def bound_selection(self):
        if self.batch is not None and self.batch.work_order_id != self.work_order.work_order_id:
            raise ValueError('批次工单不一致。')
        if self.step is not None and (self.batch is None or
            (self.step.work_order_id,self.step.batch_id)!=(self.work_order.work_order_id,self.batch.batch_id)):
            raise ValueError('步骤所属批次不一致。')
        value=self.model_dump(mode='json',exclude={'source_revision'})
        if self.source_revision!='mes-'+canonical_digest(value)[:60]:
            raise ValueError('执行来源修订不一致。')
        return self


class BatchExecutionContext(Contract):
    record: BatchExecutionRecord
    fetched_at: AwareDatetime
    last_verified_at: AwareDatetime
    source_checks: Literal[2] = 2
