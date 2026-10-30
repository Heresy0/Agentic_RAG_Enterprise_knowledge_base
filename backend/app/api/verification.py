"""Queue evaluations and record human-reviewed improvement verification; never deploy code."""
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .feedback import no_store
from .quality import checked
from ..db.dependencies import get_database_session
from ..db.verification_models import QualityEvaluationJob as Job, QualityImprovement as Improvement, QualityImprovementEvent as Event
from ..quality.reviews import utc
from ..quality.service import ObservationNotFound
from ..quality.verification import VerificationStore
from ..quality.verification_schemas import EvaluationInput, EvaluationReviewInput, ImprovementInput, ImprovementUpdate, VerificationInput, CloseInput
from ..security.dependencies import get_current_principal
from ..security.principal import Principal
from fastapi import Request

router = APIRouter(prefix="/api/quality", tags=["quality-verification"], dependencies=[Depends(no_store)])


def store(request: Request, session: Session = Depends(get_database_session), principal: Principal = Depends(get_current_principal)):
    return VerificationStore(session, principal, request.app.state.settings)


@router.post("/evaluations", status_code=201)
def create_evaluation(payload: EvaluationInput, store: VerificationStore = Depends(store)):
    with checked(store):
        job = store.create_job(payload); store.session.commit()
        return store.job_data(job)


@router.get("/evaluations")
def list_evaluations(limit: int = Query(20, ge=1, le=50), store: VerificationStore = Depends(store)):
    items = []
    ids = list(store.session.scalars(select(Job.id).where(Job.tenant_id == store.principal.tenant_id)
                                   .order_by(Job.created_at.desc(), Job.id.desc()).limit(limit)))
    for job_id in ids:
        try:
            job, current = store.get_job(job_id); items.append(store.job_data(job, current))
        except ObservationNotFound: continue
    return dict(items=items)


@router.get("/evaluations/{job_id}")
def evaluation_detail(job_id: UUID, store: VerificationStore = Depends(store)):
    with checked(store):
        job, current = store.get_job(job_id)
        return store.job_data(job, current, detail=True)


@router.post("/evaluations/{job_id}/cancel")
def cancel_evaluation(job_id: UUID, store: VerificationStore = Depends(store)):
    with checked(store):
        job = store.cancel(job_id); store.session.commit()
        return store.job_data(job)


@router.post("/evaluations/{job_id}/reviews/{sample_id}")
def review_evaluation(job_id: UUID, sample_id: str, payload: EvaluationReviewInput, store: VerificationStore = Depends(store)):
    with checked(store):
        job = store.review_answer(job_id, sample_id, payload); store.session.commit()
        return store.job_data(job, detail=True)


@router.get("/comparison")
def comparison(baseline_job_id: UUID, candidate_job_id: UUID, target_case_id: UUID | None = None,
               acknowledge_unknown_usage: bool = False, store: VerificationStore = Depends(store)):
    with checked(store):
        return store.compare(baseline_job_id, candidate_job_id, target_case_id=target_case_id,
                             acknowledge_unknown=acknowledge_unknown_usage)


@router.post("/improvements", status_code=201)
def create_improvement(payload: ImprovementInput, store: VerificationStore = Depends(store)):
    with checked(store):
        row = store.save_improvement(payload); store.session.commit()
        return store.improvement_data(row)


@router.get("/improvements")
def list_improvements(case_id: UUID | None = None, limit: int = Query(20, ge=1, le=50), store: VerificationStore = Depends(store)):
    predicates = [Improvement.tenant_id == store.principal.tenant_id]
    if case_id: predicates.append(Improvement.case_id == case_id)
    ids = list(store.session.scalars(select(Improvement.id).where(*predicates)
                                    .order_by(Improvement.updated_at.desc()).limit(limit)))
    items = []
    for item in ids:
        try: items.append(store.improvement_data(store.get_improvement(item)))
        except ObservationNotFound: continue
    return dict(items=items)


@router.get("/improvements/{improvement_id}")
def improvement_detail(improvement_id: UUID, before_revision: int | None = Query(None, ge=1), store: VerificationStore = Depends(store)):
    with checked(store):
        row = store.get_improvement(improvement_id)
        query = select(Event).where(Event.tenant_id == row.tenant_id, Event.improvement_id == row.id)
        if before_revision: query = query.where(Event.revision < before_revision)
        events = list(store.session.scalars(query.order_by(Event.revision.desc()).limit(50)))
        return dict(**store.improvement_data(row), history=[dict(revision=event.revision,
            actor_user_id=event.actor_user_id, created_at=utc(event.created_at), data=event.payload_json) for event in events],
            next_before=events[-1].revision if len(events) == 50 else None)


@router.put("/improvements/{improvement_id}")
def update_improvement(improvement_id: UUID, payload: ImprovementUpdate, store: VerificationStore = Depends(store)):
    with checked(store):
        row = store.save_improvement(payload, store.get_improvement(improvement_id, lock=True)); store.session.commit()
        return store.improvement_data(row)


@router.post("/improvements/{improvement_id}/verify")
def verify_improvement(improvement_id: UUID, payload: VerificationInput, store: VerificationStore = Depends(store)):
    with checked(store):
        row = store.verify(store.get_improvement(improvement_id, lock=True), payload); store.session.commit()
        return store.improvement_data(row)


@router.post("/improvements/{improvement_id}/close")
def close_improvement(improvement_id: UUID, payload: CloseInput, store: VerificationStore = Depends(store)):
    with checked(store):
        row = store.close(store.get_improvement(improvement_id, lock=True), payload); store.session.commit()
        return store.improvement_data(row)
