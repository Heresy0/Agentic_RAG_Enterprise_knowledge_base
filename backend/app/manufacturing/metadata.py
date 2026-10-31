"""Reviewed, source-bound runtime annotations; independent of frozen M0 gold."""
import hashlib
import json
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from sqlalchemy import select

from ..db.models import DocumentChunk, DocumentParent
from .contracts import Contract, Digest, Identifier, Text


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class SourceAnchor(Contract):
    chunk_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    vector_generation: str = Field(pattern=r"^[a-f0-9]{32}$")
    chunk_content_hash: Digest
    parent_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    parent_content_hash: Digest | None = None
    quote: str = Field(min_length=1, max_length=4000)
    quote_sha256: Digest

    @model_validator(mode="after")
    def valid_quote(self):
        if not self.quote.strip() or digest(self.quote) != self.quote_sha256:
            raise ValueError("原文摘录指纹不一致。")
        if bool(self.parent_id) != bool(self.parent_content_hash):
            raise ValueError("父块标识及指纹必须同时提供。")
        return self


class ManufacturingRule(Contract):
    rule_id: Identifier
    kind: Literal["start_before", "start_on_or_after", "work_order_override"]
    target_document_code: Identifier
    target_business_revision: Identifier
    product_model: Text
    operation_code: Text
    cutoff_date: date | None = None
    work_order_ids: tuple[Identifier, ...] = Field(default=(), max_length=20)
    effective_from: date | None = None
    supersedes_rule_ids: tuple[Identifier, ...] = Field(default=(), max_length=16)
    anchor: SourceAnchor

    @model_validator(mode="after")
    def explicit_conditions(self):
        if self.kind == "work_order_override":
            if not self.work_order_ids or self.effective_from is None or self.cutoff_date is not None:
                raise ValueError("工单通知必须列明工单及生效日期。")
        elif self.cutoff_date is None or self.work_order_ids or self.effective_from is not None or self.supersedes_rule_ids:
            raise ValueError("开工切换规则仅接受明确的实际开工日期界限。")
        if self.rule_id in self.supersedes_rule_ids:
            raise ValueError("规则不能替代自身。")
        return self


class ManufacturingMetadata(Contract):
    schema_version: Literal["manufacturing-metadata-v1"] = "manufacturing-metadata-v1"
    document_family_id: Identifier
    document_code: Identifier
    business_revision: Identifier
    source_kind: Literal["formal_document", "written_notice", "historical_record"]
    document_role: Literal["instruction", "inspection", "change_notice", "event_record", "inspection_record", "reference", "route"]
    scope_kind: Literal["company", "product_operation", "work_order"]
    product_models: tuple[Text, ...] = Field(default=(), max_length=20)
    operation_codes: tuple[Text, ...] = Field(default=(), max_length=20)
    plant_codes: tuple[Identifier, ...] = Field(default=(), max_length=20)
    route_revisions: tuple[Identifier, ...] = Field(default=(), max_length=20)
    work_order_ids: tuple[Identifier, ...] = Field(default=(), max_length=20)
    effective_from: date | None = None
    effective_to: date | None = None
    archived: bool = False
    notice_state: Literal["active", "cancelled"] = "active"
    business_timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    source_anchors: tuple[SourceAnchor, ...] = Field(min_length=1, max_length=8)
    rules: tuple[ManufacturingRule, ...] = Field(default=(), max_length=16)

    @field_validator("business_timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("必须提供明确的业务时区。") from None
        return value

    @model_validator(mode="after")
    def scoped_rules(self):
        if self.effective_from and self.effective_to and self.effective_from > self.effective_to:
            raise ValueError("适用区间倒置。")
        if self.scope_kind != "company" and (not self.product_models or not self.operation_codes):
            raise ValueError("制造文件必须明确产品与工序。")
        if self.scope_kind == "work_order" and not self.work_order_ids:
            raise ValueError("工单文件必须限定具体工单。")
        if self.work_order_ids and self.scope_kind != "work_order":
            raise ValueError("工单范围必须声明为 work_order。")
        if self.document_role == "instruction" and self.scope_kind == "company":
            raise ValueError("指导书不能默认适用所有产品。")
        if self.document_role in ("event_record", "inspection_record") and self.scope_kind != "work_order":
            raise ValueError("事件及检验记录必须限定具体工单。")
        if self.rules and self.document_role != "change_notice":
            raise ValueError("选版规则必须来自书面变更通知。")
        if self.rules and self.source_kind == "historical_record":
            raise ValueError("历史记录不能作为书面授权变更通知。")
        ids = [rule.rule_id for rule in self.rules]
        if len(set(ids)) != len(ids):
            raise ValueError("规则编号重复。")
        for rule in self.rules:
            if (rule.product_model not in self.product_models or rule.operation_code not in self.operation_codes
                    or (rule.work_order_ids and not set(rule.work_order_ids) <= set(self.work_order_ids))):
                raise ValueError("规则不得扩大文档明示的适用范围。")
        return self

    @property
    def payload_digest(self):
        return digest(json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")))

    @property
    def anchors(self):
        return tuple({anchor.model_dump_json(): anchor for anchor in
                      (*self.source_anchors, *(rule.anchor for rule in self.rules))}.values())


class ManufacturingReview(Contract):
    revision: int = Field(ge=1, strict=True)
    state: Literal["draft", "reviewed"]
    data: ManufacturingMetadata
    data_sha256: Digest
    document_version: int = Field(ge=1, strict=True)
    document_content_hash: Digest
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    review_note: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def fingerprint(self):
        if self.data_sha256 != self.data.payload_digest:
            raise ValueError("制造标注指纹不一致。")
        if self.state == "reviewed" and (self.reviewed_by is None or self.reviewed_at is None
                or self.reviewed_at.tzinfo is None):
            raise ValueError("缺少适用性复核记录。")
        return self


class MetadataInput(Contract):
    expected_document_version: int = Field(ge=1, strict=True)
    expected_revision: int = Field(ge=0, strict=True)
    state: Literal["draft", "reviewed"] = "draft"
    metadata: ManufacturingMetadata
    review_note: str = Field(min_length=1, max_length=2000)

    @field_validator("review_note")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("请填写适用性复核说明。")
        return value.strip()


def current_review(document):
    """Reject malformed, draft or stale annotations without guessing defaults."""
    try:
        review = ManufacturingReview.model_validate((document.metadata_json or {}).get("manufacturing"))
    except ValueError:
        return None
    if (review.state != "reviewed" or review.document_version != document.version
            or review.document_content_hash != document.content_hash):
        return None
    return review


def validate_anchors(session, document, metadata):
    """Check literal SQL text, generation and parent identity, never client text."""
    windows, quotes = {}, {}
    for anchor in metadata.anchors:
        chunk = session.scalar(select(DocumentChunk).where(
            DocumentChunk.tenant_id == document.tenant_id,
            DocumentChunk.knowledge_base_id == document.knowledge_base_id,
            DocumentChunk.document_id == document.id, DocumentChunk.id == anchor.chunk_id,
            DocumentChunk.vector_generation == anchor.vector_generation,
            DocumentChunk.content_hash == anchor.chunk_content_hash,
        ).execution_options(populate_existing=True))
        if chunk is None or digest(chunk.content) != anchor.chunk_content_hash:
            raise ValueError("原文分块已变化，请重新预览并标注。")
        text = chunk.content
        if anchor.parent_id:
            parent = session.scalar(select(DocumentParent).where(
                DocumentParent.id == anchor.parent_id, DocumentParent.id == chunk.parent_id,
                DocumentParent.tenant_id == document.tenant_id,
                DocumentParent.knowledge_base_id == document.knowledge_base_id,
                DocumentParent.document_id == document.id,
                DocumentParent.vector_generation == chunk.vector_generation,
                DocumentParent.document_version == document.version,
                DocumentParent.document_content_hash == document.content_hash,
                DocumentParent.content_hash == anchor.parent_content_hash,
                DocumentParent.first_chunk_index <= chunk.chunk_index,
                DocumentParent.last_chunk_index >= chunk.chunk_index,
            ).execution_options(populate_existing=True))
            if parent is None or digest(parent.content) != anchor.parent_content_hash:
                raise ValueError("原文父块已变化，请重新标注。")
            text = parent.content
        if anchor.quote not in text:
            raise ValueError("摘录必须逐字存在于对应 SQL 原文中。")
        quotes.setdefault(anchor.chunk_id, []).append(anchor.quote)
        if anchor.parent_id or anchor.chunk_id not in windows:
            windows[anchor.chunk_id] = text
    if any(quote not in windows[key] for key, values in quotes.items() for quote in values):
        raise ValueError("同一分块的摘录必须能在同一原文窗口中核对。")
