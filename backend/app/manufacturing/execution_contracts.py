"""Versioned execution selection; frozen M0 work-order schemas remain unchanged."""
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from .contracts import Contract, Identifier, Text, ManufacturingQuestion, WorkOrderRecord, WorkOrderContext, source_time

EXECUTION_SCHEMA = 'work-order-execution-v1'
StartValue = Annotated[str, StringConstraints(strict=True, max_length=40)]


class ExecutionSelection(Contract):
    operation_id: Identifier | None = Field(default=None, exclude_if=lambda value: value is None)
    job_card_id: Identifier | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode='after')
    def selected_parent(self):
        if self.job_card_id is not None and self.operation_id is None:
            raise ValueError('选择作业卡时必须指定所属工序。')
        return self


class ManufacturingExecutionQuestion(ManufacturingQuestion, ExecutionSelection):
    pass


class ExecutionContext(Contract):
    operation_id: Identifier
    operation_actual_started_at: StartValue | None
    operation_start_precision: Literal['date', 'datetime'] | None
    operation_status: Text
    job_card_id: Identifier | None = None
    job_card_actual_started_at: StartValue | None = None
    job_card_start_precision: Literal['date', 'datetime'] | None = None
    job_card_status: Text | None = None
    job_card_updated_at: AwareDatetime | None = None

    @model_validator(mode='after')
    def source_pairs(self):
        for prefix in ('operation', 'job_card'):
            value, precision = getattr(self, prefix + '_actual_started_at'), getattr(self, prefix + '_start_precision')
            if (value is None) != (precision is None):
                raise ValueError('实际开工与时间精度必须同时已知或未知。')
            if value is not None:
                source_time(value, precision)
        if self.job_card_id is None:
            if any(getattr(self, key) is not None for key in ('job_card_actual_started_at', 'job_card_start_precision', 'job_card_status', 'job_card_updated_at')):
                raise ValueError('未选择作业卡不能附带作业卡事实。')
        elif self.job_card_status is None or self.job_card_updated_at is None:
            raise ValueError('作业卡状态与源更新时间不可缺失。')
        return self


class ExecutionWorkOrderRecord(WorkOrderRecord):
    schema_version: Literal['work-order-execution-v1'] = EXECUTION_SCHEMA
    operation_code: Text
    execution: ExecutionContext


class ExecutionWorkOrderContext(WorkOrderContext):
    record: ExecutionWorkOrderRecord


class ActualStart(Contract):
    actual_started_at: StartValue | None
    actual_start_precision: Literal['date', 'datetime'] | None

    @model_validator(mode='after')
    def start_pair(self):
        if (self.actual_started_at is None) != (self.actual_start_precision is None):
            raise ValueError('实际开工与时间精度必须同时已知或未知。')
        if self.actual_started_at is not None:
            source_time(self.actual_started_at, self.actual_start_precision)
        return self


class OperationChoice(ActualStart):
    operation_id: Identifier
    operation_code: Text
    status: Text


class OperationPage(Contract):
    work_order_id: Identifier
    source_revision: Identifier
    items: tuple[OperationChoice, ...] = Field(max_length=100)

    @model_validator(mode='after')
    def unique_operations(self):
        if len({row.operation_id for row in self.items}) != len(self.items):
            raise ValueError('重复工序身份。')
        return self


class JobCardChoice(ActualStart):
    job_card_id: Identifier
    work_order_id: Identifier
    operation_id: Identifier
    operation_code: Text
    status: Text
    source_updated_at: AwareDatetime


class JobCardPage(Contract):
    work_order_id: Identifier
    operation_id: Identifier
    source_revision: Identifier
    items: tuple[JobCardChoice, ...] = Field(max_length=20)
    next_cursor: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=1024)] | None = None

    @model_validator(mode='after')
    def bound_cards(self):
        if len({row.job_card_id for row in self.items}) != len(self.items) or any(
                (row.work_order_id, row.operation_id) != (self.work_order_id, self.operation_id) for row in self.items):
            raise ValueError('作业卡身份或所属工单/工序不一致。')
        return self


def selection_kwargs(value):
    execution = getattr(value, 'execution', value)
    return {key: getattr(execution, key) for key in ('operation_id', 'job_card_id') if getattr(execution, key, None) is not None}


def record_from_payload(data):
    model = ExecutionWorkOrderRecord if isinstance(data, dict) and data.get('schema_version') == EXECUTION_SCHEMA else WorkOrderRecord
    return model.model_validate(data)


def context_from_payload(data):
    model = ExecutionWorkOrderContext if isinstance(data, dict) and isinstance(data.get('record'), dict) and data['record'].get('schema_version') == EXECUTION_SCHEMA else WorkOrderContext
    return model.model_validate(data)


def observed_context(record, **times):
    model = ExecutionWorkOrderContext if isinstance(record, ExecutionWorkOrderRecord) else WorkOrderContext
    return model(record=record, payload_sha256=record.payload_sha256, **times)
