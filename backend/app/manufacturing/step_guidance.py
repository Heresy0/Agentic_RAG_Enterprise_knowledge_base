"""Explicit reviewed instruction assignment for one exact OpenMES step."""
from dataclasses import dataclass
from datetime import timezone
from types import SimpleNamespace
from typing import Literal
from uuid import UUID

from pydantic import Field,model_validator
from sqlalchemy import select,func

from ..db.business_models import StepGuidanceRevision as Revision
from ..db.models import KnowledgeDocument
from ..documents.publication import is_published
from .batch_qa_contracts import BatchWorkOrderRecord
from .contracts import Contract,Digest,Text,Identifier,canonical_digest
from .metadata import current_review,validate_anchors
from .scope import (ManufacturingScopeChanged,ManufacturingFence,DocumentBinding,RequiredEvidence,
                    catalog_rows,catalog_fingerprint,matches_record)


class GuidanceInput(Contract):
    expected_revision: int=Field(ge=0,strict=True)
    state: Literal['draft','reviewed','withdrawn']='draft'
    document_id: UUID | None=None
    operation_code: Text | None=None
    expected_source_revision: Identifier | None=None
    review_note: str=Field(min_length=1,max_length=2000)

    @model_validator(mode='after')
    def complete(self):
        if not self.review_note.strip():raise ValueError('请填写映射复核说明。')
        if self.state!='withdrawn' and (self.document_id is None or self.operation_code is None or self.expected_source_revision is None):
            raise ValueError('请指定指导书、工艺编码及已预览来源修订。')
        return self


class GuidanceData(Contract):
    source_record: BatchWorkOrderRecord
    document_id: UUID
    document_version: int=Field(ge=1)
    document_content_hash: Digest
    manufacturing_revision: int=Field(ge=1)
    manufacturing_digest: Digest
    document_code: Identifier
    business_revision: Identifier
    operation_code: Text
    basis: Literal['reviewed_step_assignment']='reviewed_step_assignment'


def key_predicates(record):
    return [getattr(Revision,k)==getattr(record,k) for k in ('tenant_id','source_system_id','plant_code','work_order_id','batch_id','step_id')]


def latest_rows(session,scope,record):
    grouped=select(Revision.knowledge_base_id.label('kb'),func.max(Revision.revision).label('rev')).where(
        *key_predicates(record),scope.knowledge_base_filter(Revision.knowledge_base_id)).group_by(Revision.knowledge_base_id).subquery()
    return list(session.scalars(select(Revision).join(grouped,(Revision.knowledge_base_id==grouped.c.kb)&(Revision.revision==grouped.c.rev)).where(
        *key_predicates(record)).execution_options(populate_existing=True)))


def mapping_digest(rows):
    return canonical_digest(sorted((str(x.id),x.revision,x.state,x.payload_sha256) for x in rows))


def target_data(session,doc,record,operation_code):
    review=current_review(doc) if doc is not None else None
    if doc is None or doc.tenant_id!=record.tenant_id or doc.status!='ready' or not is_published(doc) or review is None:
        raise ManufacturingScopeChanged('指导书未发布或制造适用性尚未复核。')
    projected=SimpleNamespace(**(record.model_dump() | dict(operation_code=operation_code)))
    if review.data.document_role!='instruction' or operation_code not in review.data.operation_codes or not matches_record(review.data,projected):
        raise ManufacturingScopeChanged('指导书的已审核产品、工厂、工艺或工单范围不匹配。')
    validate_anchors(session,doc,review.data)
    return GuidanceData(source_record=record,document_id=doc.id,document_version=doc.version,document_content_hash=doc.content_hash,
        manufacturing_revision=review.revision,manufacturing_digest=review.data_sha256,document_code=review.data.document_code,
        business_revision=review.data.business_revision,operation_code=operation_code)


@dataclass(frozen=True)
class StepMappingGuard:
    record: BatchWorkOrderRecord
    rows_sha256: str
    selected: dict | None=None

    def require_current(self,session,scope):
        if mapping_digest(latest_rows(session,scope,self.record))!=self.rows_sha256:
            raise ManufacturingScopeChanged('步骤指导书映射已变化，请重新查询。')


def resolve_step_guidance(context,scope,session,*,include_other_revisions=False):
    from .applicability import ApplicabilityDecision
    record=context.record
    mappings=latest_rows(session,scope,record) if record.step is not None else []
    guard=StepMappingGuard(record,mapping_digest(mappings)) if record.step is not None else None
    projected=record;rows=catalog_rows(session,scope,record=record)
    def finish(state,reason,*,documents=(),required=(),selected=None,gaps=()):
        fence=ManufacturingFence(state,catalog_fingerprint(rows),tuple(documents),tuple(required),catalog_record=projected,step_mapping=guard)
        return ApplicabilityDecision(state,reason,scope_with(fence),selected.metadata.document_code if selected else None,
            selected.metadata.business_revision if selected else None,gaps=tuple(gaps))
    def scope_with(fence):
        from dataclasses import replace
        return replace(scope,manufacturing=fence)
    if record.step is None:return finish('needs_context','missing_step_selection',gaps=('step_selection',))
    active=[x for x in mappings if x.state=='reviewed']
    if len(active)>1:return finish('conflict','multiple_step_assignments')
    if not active:return finish('needs_context','missing_reviewed_step_assignment',gaps=('step_guidance_mapping',))
    mapping=active[0]
    try:
        data=GuidanceData.model_validate(mapping.payload_json['data'])
        if data.source_record!=record or canonical_digest(mapping.payload_json)!=mapping.payload_sha256:
            raise ManufacturingScopeChanged('映射来源已变化。')
        doc=session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id==data.document_id,
            KnowledgeDocument.tenant_id==scope.tenant_id,KnowledgeDocument.knowledge_base_id==mapping.knowledge_base_id).execution_options(populate_existing=True))
        if target_data(session,doc,record,data.operation_code)!=data:raise ManufacturingScopeChanged('映射指导书已变化。')
    except (ValueError,ManufacturingScopeChanged):
        return finish('needs_context','step_assignment_stale',gaps=('step_guidance_mapping',))
    projected=SimpleNamespace(**(record.model_dump() | dict(operation_code=data.operation_code)))
    rows=catalog_rows(session,scope,record=projected)
    from .applicability import CatalogEntry
    entries=[]
    for item in rows:
        review=current_review(item)
        if review is None:return finish('needs_context','published_metadata_review_invalid',gaps=('manufacturing_review',))
        try:validate_anchors(session,item,review.data)
        except ValueError:return finish('needs_context','rule_source_stale',gaps=('source_anchors',))
        entries.append(CatalogEntry(DocumentBinding.reviewed(item,review),review.data))
    selected=next(x for x in entries if x.binding.document_id==data.document_id)
    if record.route_revision is None and any(x.metadata.route_revisions for x in entries):
        return finish('needs_context','missing_route_revision',gaps=('route_revision',))
    from zoneinfo import ZoneInfo
    current=[x for x in entries if matches_record(x.metadata,projected) and (x.metadata.effective_from is None or
        x.metadata.effective_from<=context.last_verified_at.astimezone(ZoneInfo(x.metadata.business_timezone)).date()) and
        (x.metadata.effective_to is None or x.metadata.effective_to>=context.last_verified_at.astimezone(ZoneInfo(x.metadata.business_timezone)).date())]
    notices=[x for x in current if x.metadata.document_role=='change_notice' and x.metadata.notice_state=='active']
    if set(record.change_notice_refs)-{x.metadata.document_code for x in notices}:
        return finish('needs_context','referenced_notice_unavailable',gaps=('change_notice_refs',))
    # An explicit source work-order exception cannot be silently overridden by a platform mapping.
    if any(r.kind=='work_order_override' and record.work_order_id in r.work_order_ids and r.target_document_code==data.document_code
           and r.target_business_revision!=data.business_revision and r.effective_from<=context.last_verified_at.astimezone(ZoneInfo(x.metadata.business_timezone)).date()
           for x in notices for r in x.metadata.rules):return finish('conflict','step_assignment_notice_conflict')
    support=[x for x in current if x.metadata.document_role not in ('instruction','change_notice')]
    identities=[(x.metadata.document_role,x.metadata.document_family_id,x.metadata.document_code) for x in support
        if x.metadata.document_role in ('inspection','reference','route')]
    if len(identities)!=len(set(identities)):return finish('conflict','overlapping_supporting_documents')
    allowed={x.binding.document_id:x for x in (selected,*support,*notices)}
    if include_other_revisions:
        for x in entries:
            if x.metadata.document_role=='instruction' and x.metadata.document_family_id==selected.metadata.document_family_id:
                allowed[x.binding.document_id]=x
    required=tuple(RequiredEvidence(x.binding.document_id,a) for x in allowed.values() if x.metadata.document_role in ('instruction','inspection','change_notice') for a in x.metadata.source_anchors)
    if len(allowed)>32 or len(required)>24:return finish('needs_context','evidence_budget_exceeded',gaps=('catalog_scope',))
    public=dict(id=str(mapping.id),revision=mapping.revision,knowledge_base_id=str(mapping.knowledge_base_id),
        document_id=str(data.document_id),document_code=data.document_code,business_revision=data.business_revision,
        operation_code=data.operation_code,basis=data.basis,source_revision=record.source_revision,
        review_note=mapping.payload_json['review_note'],reviewed_by=str(mapping.actor_user_id),reviewed_at=mapping.created_at.replace(tzinfo=timezone.utc).isoformat())
    guard=StepMappingGuard(record,guard.rows_sha256,public)
    return finish('applicable','reviewed_step_assignment',documents=[x.binding for x in allowed.values()],required=required,selected=selected)
