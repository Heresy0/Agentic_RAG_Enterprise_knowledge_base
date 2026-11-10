"""Platform-only review writes; MES remains GET-only."""
from uuid import UUID,uuid4
from fastapi import APIRouter,Depends,HTTPException,Query,Request,Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db.dependencies import get_database_session
from ..db.models import Tenant,KnowledgeDocument
from ..db.business_models import StepGuidanceRevision as Revision
from ..audit_service import AuditService
from ..security.dependencies import get_current_principal
from ..security.principal import Principal
from ..security.authorization import AuthorizationService
from ..security.business_scope import BusinessAuthorizationService,BusinessAuthorizationDenied
from ..manufacturing.contracts import Identifier,canonical_digest
from ..manufacturing.access import WorkOrderAccessService
from ..manufacturing.step_guidance import GuidanceInput,target_data
from ..manufacturing.metadata import current_review
from ..manufacturing.scope import ManufacturingScopeChanged
from ..integrations.mes import MESContextChanged,MESSelectionUnavailable

router=APIRouter()
PATH='/work-orders/{work_order_id}/batches/{batch_id}/steps/{step_id}/guidance-mapping'


def authorized(session,principal,kb,connector):
    AuthorizationService(session).require_permission(principal=principal,knowledge_base_id=kb,required_permission='admin')
    if not connector.config.batch_step_selection:raise MESSelectionUnavailable()
    return BusinessAuthorizationService(session).require_scope(principal=principal,source_system_id=connector.config.source_system_id,permission='review')


def query(principal,kb,source,wo,batch,step,plants):
    return select(Revision).where(Revision.tenant_id==principal.tenant_id,Revision.knowledge_base_id==kb,
        Revision.source_system_id==source,Revision.work_order_id==wo,Revision.batch_id==batch,Revision.step_id==step,
        Revision.plant_code.in_(plants)).order_by(Revision.revision.desc())


def public(row):
    return dict(id=str(row.id),revision=row.revision,state=row.state,data=row.payload_json['data'],
        review_note=row.payload_json['review_note'],actor_user_id=str(row.actor_user_id),created_at=row.created_at.isoformat())


@router.get(PATH)
async def inspect_mapping(work_order_id:Identifier,batch_id:Identifier,step_id:Identifier,knowledge_base_id:UUID,
        request:Request,response:Response,before_revision:int|None=Query(None,ge=1),
        principal:Principal=Depends(get_current_principal),session:Session=Depends(get_database_session)):
    from .manufacturing import connector_for,manufacturing_operation
    identifier=uuid4().hex;response.headers.update({'Cache-Control':'no-store','X-Request-ID':identifier})
    with manufacturing_operation(session,identifier):
        connector=connector_for(request,principal);scope=authorized(session,principal,knowledge_base_id,connector)
        stmt=query(principal,knowledge_base_id,connector.config.source_system_id,work_order_id,batch_id,step_id,scope.plant_codes)
        latest=session.scalar(stmt.limit(1))
        if latest is not None and not scope.contains(principal.tenant_id,latest.source_system_id,latest.plant_code):raise BusinessAuthorizationDenied()
        if before_revision:stmt=stmt.where(Revision.revision<before_revision)
        rows=list(session.scalars(stmt.limit(51)))
        return dict(revision=latest.revision if latest else 0,mapping=public(latest) if latest else None,
            history=[public(x) for x in rows[:50]],next_before_revision=rows[49].revision if len(rows)>50 else None)


@router.get(PATH+'/candidates')
async def mapping_candidates(work_order_id:Identifier,batch_id:Identifier,step_id:Identifier,knowledge_base_id:UUID,
        request:Request,response:Response,principal:Principal=Depends(get_current_principal),session:Session=Depends(get_database_session)):
    from .manufacturing import connector_for,manufacturing_operation
    identifier=uuid4().hex;response.headers.update({'Cache-Control':'no-store','X-Request-ID':identifier})
    with manufacturing_operation(session,identifier):
        connector=connector_for(request,principal);review_scope=authorized(session,principal,knowledge_base_id,connector)
        context=await WorkOrderAccessService(session,connector).read_context(principal=principal,work_order_id=work_order_id,batch_id=batch_id,step_id=step_id)
        BusinessAuthorizationService(session).require_current(principal=principal,scope=review_scope)
        if not review_scope.contains_record(context.record):raise BusinessAuthorizationDenied()
        scope=AuthorizationService(session).require_retrieval_scope(principal=principal,knowledge_base_id=knowledge_base_id)
        from ..manufacturing.scope import catalog_rows
        values=[]
        for doc in catalog_rows(session,scope):
            review=current_review(doc)
            if review is None or review.data.document_role!='instruction':continue
            for code in review.data.operation_codes:
                try:data=target_data(session,doc,context.record,code)
                except (ValueError,ManufacturingScopeChanged):continue
                values.append(dict(document_id=str(doc.id),document_code=data.document_code,business_revision=data.business_revision,
                    operation_code=code,document_version=doc.version))
        return dict(items=values,source_revision=context.record.source_revision)


@router.put(PATH)
async def save_mapping(work_order_id:Identifier,batch_id:Identifier,step_id:Identifier,knowledge_base_id:UUID,payload:GuidanceInput,
        request:Request,response:Response,principal:Principal=Depends(get_current_principal),session:Session=Depends(get_database_session)):
    from .manufacturing import connector_for,manufacturing_operation
    identifier=uuid4().hex;response.headers.update({'Cache-Control':'no-store','X-Request-ID':identifier})
    with manufacturing_operation(session,identifier):
        connector=connector_for(request,principal);review_scope=authorized(session,principal,knowledge_base_id,connector)
        context=None
        if payload.state!='withdrawn':
            access=WorkOrderAccessService(session,connector)
            context=await access.read_context(principal=principal,work_order_id=work_order_id,batch_id=batch_id,step_id=step_id,
                expected_source_revision=payload.expected_source_revision)
            if not review_scope.contains_record(context.record):raise BusinessAuthorizationDenied()
        session.scalar(select(Tenant).where(Tenant.id==principal.tenant_id).with_for_update())
        plants={context.record.plant_code} if context is not None else review_scope.plant_codes
        previous=session.scalar(query(principal,knowledge_base_id,connector.config.source_system_id,work_order_id,batch_id,step_id,plants).limit(1).execution_options(populate_existing=True))
        if previous is not None and not review_scope.contains(principal.tenant_id,previous.source_system_id,previous.plant_code):raise BusinessAuthorizationDenied()
        if (previous.revision if previous else 0)!=payload.expected_revision:raise MESContextChanged()
        if payload.state=='withdrawn':
            if previous is None:raise MESContextChanged()
            data=previous.payload_json['data'];plant=previous.plant_code
        else:
            doc=session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id==payload.document_id,
                KnowledgeDocument.tenant_id==principal.tenant_id,KnowledgeDocument.knowledge_base_id==knowledge_base_id).execution_options(populate_existing=True))
            try:data=target_data(session,doc,context.record,payload.operation_code).model_dump(mode='json')
            except (ValueError,ManufacturingScopeChanged):raise MESContextChanged() from None
            if payload.state=='reviewed' and (previous is None or previous.state!='draft' or previous.payload_json['data']!=data):
                raise MESContextChanged()
            context=await access.verify_context(principal=principal,previous=context)
            session.refresh(doc)
            try:current_data=target_data(session,doc,context.record,payload.operation_code).model_dump(mode='json')
            except (ValueError,ManufacturingScopeChanged):raise MESContextChanged() from None
            if current_data!=data:raise MESContextChanged()
            plant=context.record.plant_code
        authorized(session,principal,knowledge_base_id,connector)
        BusinessAuthorizationService(session).require_current(principal=principal,scope=review_scope)
        value=dict(data=data,review_note=payload.review_note.strip())
        row=Revision(tenant_id=principal.tenant_id,knowledge_base_id=knowledge_base_id,source_system_id=connector.config.source_system_id,
            plant_code=plant,work_order_id=work_order_id,batch_id=batch_id,step_id=step_id,revision=payload.expected_revision+1,
            state=payload.state,payload_json=value,payload_sha256=canonical_digest(value),actor_user_id=principal.user_id)
        session.add(row);session.flush()
        AuditService(session).add(tenant_id=principal.tenant_id,actor_user_id=principal.user_id,knowledge_base_id=knowledge_base_id,
            action='manufacturing.step_guidance_reviewed',resource_type='step_guidance',resource_id=row.id,outcome='success',request_id=identifier,
            details=dict(review_state=row.state,manufacturing_revision=row.revision))
        session.commit()
        return dict(revision=row.revision,mapping=public(row))
