"""Versioned work-order contracts; no connectors, authorization grants or model calls.

Source facts and client questions are different types. Validation preserves date
precision and never substitutes a reporting date for the actual start date.
"""
from datetime import date, datetime
import hashlib
import json
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


Identifier = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=64,
    pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
Text = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=80)]
Digest = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-f0-9]{64}$")]
SOURCE_SCHEMA = "work-order-v1"
FIXTURE_VERSION = "manufacturing-workorder-v1-r1"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def canonical_digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def source_time(value: str, precision: str | None = None) -> date | datetime:
    """Accept ISO dates or timezone-qualified timestamps, without guessing precision."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        if precision not in (None, "date"):
            raise ValueError("actual start precision does not match a date")
        return date.fromisoformat(value)
    if precision == "date" or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value):
        raise ValueError("source timestamp must include time and timezone")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("source timestamp must have a timezone")
    return parsed


def aware_source_timestamp(value):
    if isinstance(value, str):
        parsed = source_time(value, "datetime")
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise ValueError("timestamp must be an ISO string or an aware datetime")
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must have a timezone")
    return parsed


class WorkOrderRecord(Contract):
    schema_version: Literal["work-order-v1"] = SOURCE_SCHEMA
    source_system_id: Identifier
    tenant_id: UUID
    plant_code: Identifier
    work_order_id: Identifier
    source_record_id: Identifier
    product_model: Text | None
    operation_code: Text | None
    route_revision: Text | None = None
    batch_no: Text | None = None
    actual_started_at: Annotated[str, StringConstraints(strict=True, max_length=40)] | None
    actual_start_precision: Literal["date", "datetime"] | None
    reported_at: Annotated[str, StringConstraints(strict=True, max_length=40)] | None = None
    status: Text
    change_notice_refs: tuple[Identifier, ...] = Field(default=(), max_length=16)
    source_revision: Identifier
    source_updated_at: AwareDatetime

    @field_validator("source_updated_at", mode="before")
    @classmethod
    def timestamp_type(cls, value):
        return aware_source_timestamp(value)

    @model_validator(mode="after")
    def validate_times_and_references(self):
        if (self.actual_started_at is None) != (self.actual_start_precision is None):
            raise ValueError("actual start value and precision must both be present or unknown")
        if self.actual_started_at is not None:
            source_time(self.actual_started_at, self.actual_start_precision)
        if self.reported_at is not None:
            source_time(self.reported_at)
        if len(self.change_notice_refs) != len(set(self.change_notice_refs)):
            raise ValueError("duplicate change notice reference")
        return self

    @property
    def payload_sha256(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


class WorkOrderContext(Contract):
    """A source record plus server-generated observation metadata."""
    record: WorkOrderRecord
    fetched_at: AwareDatetime
    last_verified_at: AwareDatetime
    payload_sha256: Digest

    @field_validator("fetched_at", "last_verified_at", mode="before")
    @classmethod
    def timestamp_type(cls, value):
        return aware_source_timestamp(value)

    @model_validator(mode="after")
    def validate_observation(self):
        if self.last_verified_at < self.fetched_at:
            raise ValueError("verification precedes the source fetch")
        if self.payload_sha256 != self.record.payload_sha256:
            raise ValueError("source payload fingerprint mismatch")
        return self


class ManufacturingQuestion(Contract):
    question: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=1000)]
    work_order_id: Identifier
    conversation_id: UUID | None = None
    knowledge_base_id: UUID | None = None
    expected_source_revision: Identifier | None = None

    @model_validator(mode="after")
    def nonblank_question(self):
        if not self.question.strip():
            raise ValueError("question cannot be blank")
        return self


class WorkOrderFixtures(Contract):
    schema_version: Literal["work-order-fixtures-v1"]
    fixture_version: Identifier
    source_system_id: Identifier
    tenant_id: UUID
    records: tuple[WorkOrderRecord, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_record_identities(self):
        keys = [r.work_order_id for r in self.records]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate work order identity")
        if len({r.source_record_id for r in self.records}) != len(self.records):
            raise ValueError("duplicate source record identity")
        if any(r.tenant_id != self.tenant_id or r.source_system_id != self.source_system_id
               for r in self.records):
            raise ValueError("source or tenant mismatch in fixtures")
        return self


class ApplicabilityRule(Contract):
    """Auditable source annotation, not a question-specific answer override."""
    rule_id: Identifier
    kind: Literal["start_before", "start_on_or_after", "work_order_override"]
    target_document_code: Identifier
    target_business_revision: Text
    product_model: Text
    operation_code: Text
    cutoff_date: date | None = None
    work_order_ids: tuple[Identifier, ...] = ()
    effective_from: date | None = None
    source_document_id: Identifier
    source_quote: Annotated[str, StringConstraints(strict=True, min_length=2, max_length=2000)]
    source_quote_sha256: Digest
    review_state: Literal["draft"] = "draft"

    @model_validator(mode="after")
    def validate_source_and_conditions(self):
        if hashlib.sha256(self.source_quote.encode("utf-8")).hexdigest() != self.source_quote_sha256:
            raise ValueError("rule quote fingerprint mismatch")
        if self.kind == "work_order_override":
            if not self.work_order_ids or self.effective_from is None or self.cutoff_date is not None:
                raise ValueError("override requires explicit work orders and effective date")
        elif self.cutoff_date is None or self.work_order_ids or self.effective_from is not None:
            raise ValueError("start-window rule requires only a cutoff date")
        if len(set(self.work_order_ids)) != len(self.work_order_ids):
            raise ValueError("duplicate override identity")
        return self


def contract_schemas() -> dict:
    return {model.__name__: model.model_json_schema() for model in (
        WorkOrderRecord, WorkOrderContext, ManufacturingQuestion, WorkOrderFixtures, ApplicabilityRule)}
