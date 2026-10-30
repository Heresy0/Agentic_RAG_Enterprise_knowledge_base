"""Human review queue, immutable revisions and explicitly authorized dataset exports."""
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .feedback import no_store
from ..db.dependencies import get_database_session
from ..db.quality_models import QARun, QARunScope, QAFeedback
from ..db.review_models import QualityCase, QualityCaseRevision, QualityDatasetVersion
from ..quality.review_schemas import DatasetInput, OpenCaseInput, ReviewInput, ReviewStatus
from ..quality.reviews import ReviewConflict, ReviewStore, feedback_snapshot, utc
from ..quality.service import ObservationNotFound
from ..security.dependencies import get_current_principal
from ..security.principal import Principal

router = APIRouter(prefix="/api/quality", tags=["quality-review"], dependencies=[Depends(no_store)])


def get_store(session: Session = Depends(get_database_session), principal: Principal = Depends(get_current_principal)):
    return ReviewStore(session, principal)


@contextmanager
def checked(store):
    try:
        yield
    except ObservationNotFound:
        store.session.rollback()
        raise HTTPException(404, "未找到可访问的审核记录或样本版本。") from None
    except ReviewConflict as exc:
        store.session.rollback()
        raise HTTPException(409, str(exc)) from None
    except Exception:
        store.session.rollback()
        raise


def detail(store, case, run, feedback, *, history_before=None, history_limit=20):
    query = select(QualityCaseRevision).where(QualityCaseRevision.tenant_id == case.tenant_id,
                                            QualityCaseRevision.case_id == case.id)
    if history_before is not None:
        query = query.where(QualityCaseRevision.revision < history_before)
    revisions = list(store.session.scalars(query.order_by(QualityCaseRevision.revision.desc()).limit(history_limit)))
    return dict(case=store.case_data(case, run, feedback), review=store.revision(case).payload_json,
        source=dict(run_id=run.id, question=run.question, retrieval_question=run.retrieval_question,
                    response=run.response_json, snapshot=run.snapshot_json, snapshot_status=run.snapshot_status,
                    feedback=feedback_snapshot(feedback)),
        history=[dict(revision=r.revision, reviewer_user_id=r.reviewer_user_id, created_at=utc(r.created_at),
                      review=r.payload_json, source_binding=r.source_binding) for r in revisions],
        next_history_before=revisions[-1].revision if len(revisions) == history_limit else None)


@router.get("/cases")
def list_cases(review_status: ReviewStatus | None = None, knowledge_base_id: UUID | None = None,
               before: datetime | None = None, before_id: UUID | None = None,
               limit: int = Query(20, ge=1, le=100), store: ReviewStore = Depends(get_store)):
    if (before is None) != (before_id is None) or before is not None and before.tzinfo is None:
        raise HTTPException(422, "游标须同时提供带时区的 before 和 before_id。")
    access = store.feedback_store
    predicates = access.scope_predicates(access.allowed_ids(admin=True))
    predicates += [QAFeedback.tenant_id == store.principal.tenant_id, QAFeedback.share_with_admins.is_(True)]
    if knowledge_base_id is not None:
        predicates.append(QARun.id.in_(select(QARunScope.run_id).where(
            QARunScope.tenant_id == store.principal.tenant_id, QARunScope.knowledge_base_id == knowledge_base_id)))
    if review_status:
        predicates.append(func.coalesce(QualityCase.review_status, "pending") == review_status)
    updated = func.coalesce(QualityCase.updated_at, QAFeedback.updated_at)
    if before is not None:
        predicates.append((updated < before) | ((updated == before) & (QARun.id < before_id)))
    rows = store.session.execute(select(QARun, QAFeedback, QualityCase, updated.label("queue_updated"))
        .join(QAFeedback, (QAFeedback.run_id == QARun.id) & (QAFeedback.tenant_id == QARun.tenant_id))
        .outerjoin(QualityCase, (QualityCase.run_id == QARun.id) & (QualityCase.tenant_id == QARun.tenant_id))
        .where(*predicates).order_by(updated.desc(), QARun.id.desc()).limit(limit)).all()
    items = []
    for run, feedback, case, timestamp in rows:
        if not access.sources_available(run):
            continue
        values = store.case_data(case, run, feedback) if case else dict(id=None, revision=0,
            review_status="pending", sample_status="draft", root_cause=None,
            source_changed=False, export_eligible=False, export_blocked_reason="not_approved")
        items.append(dict(values, run_id=run.id, question=run.question, feedback=feedback_snapshot(feedback),
                          updated_at=utc(timestamp)))
    return dict(items=items, next_cursor=dict(before=utc(rows[-1].queue_updated), before_id=rows[-1][0].id) if len(rows) == limit else None)


@router.post("/cases")
def open_case(payload: OpenCaseInput, response: Response, store: ReviewStore = Depends(get_store)):
    with checked(store):
        case, run, feedback, created = store.open_case(payload.run_id)
        store.session.commit()
        response.status_code = 201 if created else 200
        return detail(store, case, run, feedback)


@router.get("/cases/{case_id}")
def get_case(case_id: UUID, history_before: int | None = Query(None, ge=1),
             history_limit: int = Query(20, ge=1, le=50), store: ReviewStore = Depends(get_store)):
    with checked(store):
        return detail(store, *store.get_case(case_id), history_before=history_before, history_limit=history_limit)


@router.put("/cases/{case_id}")
def review_case(case_id: UUID, payload: ReviewInput, store: ReviewStore = Depends(get_store)):
    with checked(store):
        case, run, feedback = store.update_case(case_id, payload)
        store.session.commit()
        return detail(store, case, run, feedback)


def version_data(dataset, *, current=True):
    return dict(id=dataset.id, name=dataset.name, created_at=utc(dataset.created_at), current=current,
                manifest=dataset.manifest_json)


@router.post("/datasets", status_code=201)
def create_dataset(payload: DatasetInput, store: ReviewStore = Depends(get_store)):
    with checked(store):
        dataset = store.create_dataset(payload)
        store.session.commit()
        return version_data(dataset)


@router.get("/datasets")
def list_datasets(limit: int = Query(10, ge=1, le=20), store: ReviewStore = Depends(get_store)):
    rows = store.session.scalars(select(QualityDatasetVersion).where(
        QualityDatasetVersion.tenant_id == store.principal.tenant_id).order_by(
        QualityDatasetVersion.created_at.desc(), QualityDatasetVersion.id.desc()).limit(limit))
    items = []
    for dataset in rows:
        try:
            current = store.dataset_content(dataset, require_current=False) is not None
        except ObservationNotFound:
            continue
        items.append(version_data(dataset, current=current))
    return dict(items=items)


@router.get("/datasets/{dataset_id}/manifest")
def dataset_manifest(dataset_id: UUID, store: ReviewStore = Depends(get_store)):
    with checked(store):
        dataset = store.get_dataset(dataset_id)
        store.dataset_content(dataset)
        store.audit("quality.manifest_downloaded", dataset.id); store.session.commit()
        return version_data(dataset)


@router.get("/datasets/{dataset_id}/samples")
def dataset_samples(dataset_id: UUID, store: ReviewStore = Depends(get_store)):
    with checked(store):
        dataset = store.get_dataset(dataset_id)
        content = store.dataset_content(dataset)
        store.audit("quality.samples_downloaded", dataset.id); store.session.commit()
        return Response(content, media_type="application/x-ndjson", headers={"Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="feedback-regression-{dataset.id}.jsonl"'})
