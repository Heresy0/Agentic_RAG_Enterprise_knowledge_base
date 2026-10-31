"""A request-local, fail-closed document fence computed by applicability review."""
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, false, or_, select

from ..db.models import KnowledgeDocument
from ..documents.publication import is_published, publication
from .contracts import canonical_digest
from .metadata import SourceAnchor, current_review, validate_anchors


class ManufacturingScopeChanged(RuntimeError):
    pass


def catalog_rows(session, scope):
    rows = list(session.scalars(select(KnowledgeDocument).where(
        KnowledgeDocument.tenant_id == scope.tenant_id,
        scope.knowledge_base_filter(KnowledgeDocument.knowledge_base_id),
        KnowledgeDocument.status != "deleted",
        KnowledgeDocument.metadata_json["manufacturing"].as_string().is_not(None),
    ).limit(257).execution_options(populate_existing=True)))
    rows = [doc for doc in rows if "manufacturing" in (doc.metadata_json or {})]
    if len(rows) > 256:
        raise ManufacturingScopeChanged("制造目录超过首期单次处理上限，请缩小知识库范围。")
    return sorted(rows, key=lambda doc: str(doc.id))


def catalog_fingerprint(rows):
    return canonical_digest([dict(id=str(doc.id), knowledge_base_id=str(doc.knowledge_base_id),
        version=doc.version, content_hash=doc.content_hash, status=doc.status,
        publication=publication(doc), manufacturing=(doc.metadata_json or {}).get("manufacturing")) for doc in rows])


@dataclass(frozen=True, slots=True)
class DocumentBinding:
    document_id: UUID
    knowledge_base_id: UUID
    document_version: int
    document_content_hash: str
    manufacturing_revision: int
    manufacturing_digest: str
    anchors: tuple[SourceAnchor, ...]
    document_code: str | None = None
    business_revision: str | None = None
    document_role: str | None = None

    @classmethod
    def reviewed(cls, doc, review):
        return cls(doc.id, doc.knowledge_base_id, doc.version, doc.content_hash,
                   review.revision, review.data_sha256, review.data.anchors,
                   review.data.document_code, review.data.business_revision, review.data.document_role)

    def predicate(self):
        m = KnowledgeDocument.metadata_json["manufacturing"]
        return and_(KnowledgeDocument.id == self.document_id,
            KnowledgeDocument.knowledge_base_id == self.knowledge_base_id,
            KnowledgeDocument.version == self.document_version,
            KnowledgeDocument.content_hash == self.document_content_hash,
            m["state"].as_string() == "reviewed",
            m["revision"].as_integer() == self.manufacturing_revision,
            m["data_sha256"].as_string() == self.manufacturing_digest,
            m["document_version"].as_integer() == self.document_version,
            m["document_content_hash"].as_string() == self.document_content_hash)


@dataclass(frozen=True, slots=True)
class RequiredEvidence:
    document_id: UUID
    anchor: SourceAnchor


@dataclass(frozen=True, slots=True)
class ManufacturingFence:
    state: str
    catalog_sha256: str
    documents: tuple[DocumentBinding, ...] = ()
    required_evidence: tuple[RequiredEvidence, ...] = ()
    explanation_documents: tuple[DocumentBinding, ...] = ()
    explanation_evidence: tuple[RequiredEvidence, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "documents", tuple(self.documents))
        object.__setattr__(self, "required_evidence", tuple(self.required_evidence))
        object.__setattr__(self, "explanation_documents", tuple(self.explanation_documents))
        object.__setattr__(self, "explanation_evidence", tuple(self.explanation_evidence))
        if self.state not in ("applicable", "needs_context", "no_evidence", "conflict"):
            raise ValueError("无效的制造适用性状态。")
        if self.state != "applicable" and (self.documents or self.required_evidence):
            raise ValueError("不确定的适用性不能开放正文检索。")
        if len(self.documents) > 32 or len(self.required_evidence) > 24:
            raise ValueError("制造证据超过首期处理上限。")
        if len(self.explanation_documents) > 32 or len(self.explanation_evidence) > 24:
            raise ValueError("制造冲突说明超过首期处理上限。")
        if len(self.document_ids) != len(self.documents):
            raise ValueError("制造文档绑定不能重复。")
        if any(row.document_id not in self.document_ids for row in self.required_evidence):
            raise ValueError("必需证据必须位于适用文档范围内。")

    @property
    def document_ids(self):
        return frozenset(row.document_id for row in self.documents)

    def predicate(self):
        return or_(*(row.predicate() for row in self.documents)) if self.documents else false()

    def contains_metadata(self, metadata):
        return any(str(row.document_id) == str(metadata.get("document_id"))
            and str(row.knowledge_base_id) == str(metadata.get("knowledge_base_id"))
            and row.document_version == metadata.get("document_version")
            and row.document_content_hash == metadata.get("document_content_hash")
            and row.manufacturing_revision == metadata.get("manufacturing_revision")
            and row.manufacturing_digest == metadata.get("manufacturing_digest") for row in self.documents)

    def require_current(self, session, scope):
        """Recheck the full authorized catalog, including newly published notices.

        Losing a deciding ECN must invalidate the whole selection; it cannot just
        disappear while the old instruction continues to authorize an answer.
        """
        rows = catalog_rows(session, scope)
        if catalog_fingerprint(rows) != self.catalog_sha256:
            raise ManufacturingScopeChanged("制造目录或发布状态已变化，请重新判断适用性。")
        by_id = {doc.id: doc for doc in rows}
        for binding in (*self.documents, *self.explanation_documents):
            doc = by_id.get(binding.document_id)
            review = current_review(doc) if doc is not None else None
            if doc is None or doc.status != "ready" or not is_published(doc) or review is None:
                raise ManufacturingScopeChanged("制造文件或复核状态已变化。")
            try:
                validate_anchors(session, doc, review.data)
            except ValueError:
                raise ManufacturingScopeChanged("选版依据原文已变化，请重新复核。") from None
