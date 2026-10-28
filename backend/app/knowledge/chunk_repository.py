from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..db.models import (
    EMBEDDING_DIMENSION,
    DocumentChunk,
    DocumentParent,
    KnowledgeDocument,
)
from ..security.retrieval_scope import RetrievalScope
from ..documents.policy_metadata import extract_policy
from ..documents.parent_chunks import body_text


@dataclass(frozen=True, slots=True)
class StoredChunk:
    """当前知识库中的一个可检索分块。"""

    chunk_id: str
    document_id: UUID
    tenant_id: UUID
    knowledge_base_id: UUID
    content: str
    document_name: str
    source: str
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class VectorSearchHit(StoredChunk):
    """一次向量检索返回的文档分块。"""

    distance: float


class ChunkRepository:
    """使用 SQL 确认权限与当前版本，再从 Milvus 检索向量。"""

    def __init__(
        self,
        session: Session,
        vector_store=None,
    ) -> None:
        self._session = session
        self._vector_store = vector_store

    @staticmethod
    def _build_metadata(
        *,
        chunk: DocumentChunk,
        document: KnowledgeDocument,
    ) -> dict[str, object]:
        metadata = dict(chunk.metadata_json or {})
        stored_policy = (document.metadata_json or {}).get("policy")
        if isinstance(stored_policy, dict):
            metadata["policy"] = stored_policy

        metadata.update({
            "tenant_id": str(chunk.tenant_id),
            "knowledge_base_id": str(
                chunk.knowledge_base_id
            ),
            "document_id": str(document.id),
            "document_version": document.version,
            "source_id": document.source_id,
            "chunk_id": chunk.id,
            "chunk_index": chunk.chunk_index,
            "chunk_content_hash": chunk.content_hash,
            "vector_generation": chunk.vector_generation,
            "document_name": document.file_name,
            "source": document.storage_uri,
        })

        return metadata

    def candidate_policies(self, *, scope, document_ids):
        """Read headers of candidate documents only, with scope/ready guards.

        Read at most the first eight chunks per candidate. Legacy indexes need no
        embedding rebuild. Extracted policy is request-local;
        this method never persists metadata or loads the whole knowledge base.
        """
        if not document_ids:
            return {}
        rows = self._session.execute(select(KnowledgeDocument, DocumentChunk).join(
            DocumentChunk, and_(KnowledgeDocument.id == DocumentChunk.document_id,
                KnowledgeDocument.tenant_id == DocumentChunk.tenant_id,
                KnowledgeDocument.knowledge_base_id == DocumentChunk.knowledge_base_id),
        ).where(KnowledgeDocument.id.in_(document_ids), KnowledgeDocument.tenant_id == scope.tenant_id,
                scope.knowledge_base_filter(KnowledgeDocument.knowledge_base_id),
                KnowledgeDocument.status == "ready", DocumentChunk.chunk_index < 8
                ).order_by(KnowledgeDocument.id, DocumentChunk.chunk_index)).all()
        grouped = {}
        for document, chunk in rows:
            grouped.setdefault(str(document.id), (document, []))[1].append(chunk.content)
        return {identifier: {**extract_policy("\n".join(contents), doc.file_name),
                            **((doc.metadata_json or {}).get("policy") if isinstance((doc.metadata_json or {}).get("policy"), dict) else {})}
                for identifier, (doc, contents) in grouped.items()}

    def _current_rows(self, scope):
        return select(DocumentChunk, KnowledgeDocument).join(KnowledgeDocument, and_(
            KnowledgeDocument.id == DocumentChunk.document_id,
            KnowledgeDocument.tenant_id == DocumentChunk.tenant_id,
            KnowledgeDocument.knowledge_base_id == DocumentChunk.knowledge_base_id,
        )).where(DocumentChunk.tenant_id == scope.tenant_id,
                 scope.knowledge_base_filter(DocumentChunk.knowledge_base_id),
                 KnowledgeDocument.status == "ready").execution_options(populate_existing=True)

    def current_chunks(self, *, scope, expected):
        """Rehydrate a bounded evidence pool; retired versions cannot re-enter."""
        if not expected:
            return []
        if len(expected) > 32:
            raise ValueError("证据池最多允许 32 个分块")
        rows = self._session.execute(self._current_rows(scope).where(DocumentChunk.id.in_(expected))).all()
        result = {}
        for chunk, document in rows:
            previous = expected[chunk.id]
            if (previous.get("vector_generation") != chunk.vector_generation
                    or str(previous.get("document_id")) != str(document.id)
                    or str(previous.get("knowledge_base_id")) != str(chunk.knowledge_base_id)):
                continue
            result[chunk.id] = self._stored(chunk, document)
        return [result[key] for key in expected if key in result]

    def neighboring_chunks(self, *, scope, chunk_id, generation, document_id, radius=2):
        """Read at most five neighbors of one already-authorized current anchor."""
        if not 0 <= radius <= 2:
            raise ValueError("相邻原文窗口最多为 2")
        anchor = self._session.execute(self._current_rows(scope).where(
            DocumentChunk.id == chunk_id, DocumentChunk.document_id == document_id,
            DocumentChunk.vector_generation == generation,
        )).first()
        if anchor is None:
            return []
        chunk, _ = anchor
        rows = self._session.execute(self._current_rows(scope).where(
            DocumentChunk.document_id == document_id, DocumentChunk.vector_generation == generation,
            DocumentChunk.chunk_index.between(max(0, chunk.chunk_index - radius), chunk.chunk_index + radius),
        ).order_by(DocumentChunk.chunk_index).limit(5)).all()
        return [self._stored(c, d) for c, d in rows]

    def parent_chunks(self, *, scope, expected):
        """Resolve at most 32 current anchors to scoped, generation-bound parents.

        The anchor child ID remains the citation address; parent ID is explicit.
        Missing legacy parents fall back to child text. A previously expanded
        parent disappearing/changing is stale evidence and is dropped.
        """
        current = self.current_chunks(scope=scope, expected=expected)
        if not current:
            return []
        ids = [row.chunk_id for row in current]
        join = and_(DocumentParent.id==DocumentChunk.parent_id,
            DocumentParent.tenant_id==DocumentChunk.tenant_id,
            DocumentParent.knowledge_base_id==DocumentChunk.knowledge_base_id,
            DocumentParent.document_id==DocumentChunk.document_id,
            DocumentParent.vector_generation==DocumentChunk.vector_generation,
            DocumentParent.document_version==KnowledgeDocument.version,
            DocumentParent.document_content_hash==KnowledgeDocument.content_hash,
            DocumentParent.first_chunk_index<=DocumentChunk.chunk_index,
            DocumentParent.last_chunk_index>=DocumentChunk.chunk_index)
        rows = self._session.execute(self._current_rows(scope).add_columns(DocumentParent)
            .join(DocumentParent, join).where(DocumentChunk.id.in_(ids))).all()
        parents = {c.id:p for c, _, p in rows}
        result, seen = [], set()
        for row in current:
            parent = parents.get(row.chunk_id)
            previous = expected[row.chunk_id]
            if previous.get('parent_id') and (parent is None or parent.id!=previous['parent_id']
                or (previous.get('parent_content_hash') and parent.content_hash!=previous['parent_content_hash'])):
                continue
            if parent is None:
                result.append(row)
                continue
            if parent.id in seen:
                continue
            seen.add(parent.id)
            metadata = {**row.metadata, 'parent_id':parent.id, 'parent_content_hash':parent.content_hash,
                'child_content_hash':row.metadata['chunk_content_hash'], 'chunk_content_hash':parent.content_hash,
                'matched_child_content':body_text(row.content),
                'matched_child_section':row.metadata.get('section_path', ''),
                'section_path':parent.metadata_json.get('section_path',''), 'chunk_type':'parent',
                'parent_first_index':parent.first_chunk_index, 'parent_last_index':parent.last_chunk_index}
            result.append(StoredChunk(chunk_id=row.chunk_id, document_id=row.document_id,
                tenant_id=row.tenant_id, knowledge_base_id=row.knowledge_base_id,
                content=parent.content, document_name=row.document_name, source=row.source, metadata=metadata))
        return result

    def _stored(self, chunk, document):
        return StoredChunk(chunk_id=chunk.id, document_id=document.id, tenant_id=chunk.tenant_id,
            knowledge_base_id=chunk.knowledge_base_id, content=chunk.content, document_name=document.file_name,
            source=document.storage_uri, metadata=self._build_metadata(chunk=chunk, document=document))

    def candidate_companions(self, *, scope, document_ids):
        """First eight chunks of at most two already matched documents, scoped."""
        ids = list(document_ids)
        if not ids:
            return []
        if len(ids) > 2:
            raise ValueError("正文补取最多允许两份候选文档")
        rows = self._session.execute(select(DocumentChunk, KnowledgeDocument).join(
            KnowledgeDocument, and_(KnowledgeDocument.id == DocumentChunk.document_id,
                KnowledgeDocument.tenant_id == DocumentChunk.tenant_id,
                KnowledgeDocument.knowledge_base_id == DocumentChunk.knowledge_base_id),
        ).where(DocumentChunk.document_id.in_(ids), DocumentChunk.tenant_id == scope.tenant_id,
                scope.knowledge_base_filter(DocumentChunk.knowledge_base_id), KnowledgeDocument.status == 'ready',
                DocumentChunk.chunk_index >= 0, DocumentChunk.chunk_index < 8,
        ).order_by(KnowledgeDocument.id, DocumentChunk.chunk_index).limit(16)).all()
        return [StoredChunk(chunk_id=chunk.id, document_id=document.id, tenant_id=chunk.tenant_id,
                            knowledge_base_id=chunk.knowledge_base_id, content=chunk.content,
                            document_name=document.file_name, source=document.storage_uri,
                            metadata=self._build_metadata(chunk=chunk, document=document))
                for chunk, document in rows]

    def search(
        self,
        *,
        scope: RetrievalScope,
        query_embedding: Sequence[float],
        limit: int = 30,
    ) -> list[VectorSearchHit]:
        query_vector = self._validated_query_vector(
            query_embedding
        )

        if not 1 <= limit <= 100:
            raise ValueError(
                "向量检索limit必须在1到100之间"
            )

        if not scope.knowledge_base_ids:
            return []
        base = select(DocumentChunk, KnowledgeDocument).join(KnowledgeDocument, and_(
            KnowledgeDocument.tenant_id == DocumentChunk.tenant_id,
            KnowledgeDocument.knowledge_base_id == DocumentChunk.knowledge_base_id,
            KnowledgeDocument.id == DocumentChunk.document_id,
        )).where(DocumentChunk.tenant_id == scope.tenant_id,
                 scope.knowledge_base_filter(DocumentChunk.knowledge_base_id),
                 KnowledgeDocument.status == "ready")
        generations = self._session.scalars(base.with_only_columns(DocumentChunk.vector_generation).distinct()).all()
        if not generations:
            return []
        if self._vector_store is None:
            from .milvus_store import VectorStoreUnavailable
            raise VectorStoreUnavailable("未初始化 Milvus 向量存储")
        candidates = self._vector_store.search(scope=scope, generations=generations, vector=query_vector, limit=limit)
        if not candidates:
            return []
        ids = [row.get("chunk_id") for row in candidates if isinstance(row.get("chunk_id"), str)]
        rows = self._session.execute(base.where(DocumentChunk.id.in_(ids))).all()
        authoritative = {chunk.id: (chunk, document) for chunk, document in rows}
        hits, seen = [], set()
        for candidate in candidates:
            chunk_id = candidate.get("chunk_id")
            if chunk_id in seen or chunk_id not in authoritative:
                continue
            chunk, document = authoritative[chunk_id]
            if any(candidate.get(key) != value for key, value in {
                "tenant_id": str(chunk.tenant_id), "knowledge_base_id": str(chunk.knowledge_base_id),
                "document_id": str(document.id), "generation": chunk.vector_generation,
            }.items()):
                continue
            score = candidate.get("distance")
            if isinstance(score, bool) or not isinstance(score, (float, int)) or not isfinite(score):
                continue
            seen.add(chunk_id)
            hits.append(VectorSearchHit(chunk_id=chunk.id, document_id=document.id, tenant_id=chunk.tenant_id,
                knowledge_base_id=chunk.knowledge_base_id, content=chunk.content,
                document_name=document.file_name, source=document.storage_uri,
                metadata=self._build_metadata(chunk=chunk, document=document), distance=1.0 - float(score)))
        return hits[:limit]

    def has_ready_chunks(self, *, scope: RetrievalScope) -> bool:
        """Empty-scope check without loading the entire knowledge base."""
        statement = select(DocumentChunk.id).join(KnowledgeDocument, and_(
            KnowledgeDocument.tenant_id == DocumentChunk.tenant_id,
            KnowledgeDocument.knowledge_base_id == DocumentChunk.knowledge_base_id,
            KnowledgeDocument.id == DocumentChunk.document_id,
        )).where(
            DocumentChunk.tenant_id == scope.tenant_id,
            scope.knowledge_base_filter(DocumentChunk.knowledge_base_id),
            KnowledgeDocument.status == "ready",
        ).limit(1)
        return self._session.scalar(statement) is not None

    def list_chunks(
        self,
        *,
        scope: RetrievalScope,
    ) -> list[StoredChunk]:
        """列出授权范围内全部可用于关键词检索的分块。"""
        statement = (
            select(
                DocumentChunk,
                KnowledgeDocument,
            )
            .join(
                KnowledgeDocument,
                and_(
                    KnowledgeDocument.tenant_id
                    == DocumentChunk.tenant_id,
                    KnowledgeDocument.knowledge_base_id
                    == DocumentChunk.knowledge_base_id,
                    KnowledgeDocument.id
                    == DocumentChunk.document_id,
                ),
            )
            .where(
                DocumentChunk.tenant_id
                == scope.tenant_id,
                scope.knowledge_base_filter(DocumentChunk.knowledge_base_id),
                KnowledgeDocument.tenant_id
                == scope.tenant_id,
                scope.knowledge_base_filter(KnowledgeDocument.knowledge_base_id),
                KnowledgeDocument.status == "ready",
            )
            .order_by(
                KnowledgeDocument.id,
                DocumentChunk.chunk_index,
            )
        )

        rows = self._session.execute(
            statement
        ).all()

        chunks: list[StoredChunk] = []

        for chunk, document in rows:
            chunks.append(
                StoredChunk(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    tenant_id=chunk.tenant_id,
                    knowledge_base_id=(
                        chunk.knowledge_base_id
                    ),
                    content=chunk.content,
                    document_name=document.file_name,
                    source=document.storage_uri,
                    metadata=self._build_metadata(
                        chunk=chunk,
                        document=document,
                    ),
                )
            )

        return chunks

    @staticmethod
    def _validated_query_vector(
        values: Sequence[float],
    ) -> list[float]:
        if len(values) != EMBEDDING_DIMENSION:
            raise ValueError(
                "查询向量维度必须为"
                f"{EMBEDDING_DIMENSION}"
            )

        result: list[float] = []

        for value in values:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(float(value))
            ):
                raise ValueError(
                    "查询向量只能包含有限数值"
                )

            result.append(float(value))

        return result
