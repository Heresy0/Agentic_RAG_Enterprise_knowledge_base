from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from sqlalchemy.orm import Session

from ..config import Settings
from .hybrid_retriever import (
    create_hybrid_retriever,
)
from .milvus_retriever import (
    MilvusRetriever,
    stored_chunk_to_document,
)
from .keyword_repository import PgKeywordRepository
from .pg_bm25_retriever import PgBM25Retriever
from .rag import create_embeddings
from .reranker import BaseReranker, QwenReranker
from .rerank_retriever import RerankRetriever
from ..security.retrieval_scope import RetrievalScope
from .chunk_repository import ChunkRepository
from .milvus_store import MilvusVectorStore
from .evidence_selection import select_evidence
from .evidence_expansion import expand_incident_candidates
from uuid import UUID
from ..model_usage import audited_scope
from ..evaluation.trace import record_documents, record_scope


FINAL_K = 8
FETCH_K = 30
RANK_METADATA_KEYS = ("rerank_score", "rerank_status", "retrieval_latency_ms", "rerank_latency_ms",
                      "rrf_rank", "rrf_score", "model_rank", "final_rank")


class RetrievalService:
    """在授权范围内执行企业知识库检索。"""

    def __init__(
        self,
        settings: Settings,
        *,
        embeddings: Embeddings | None = None,
        reranker: BaseReranker | None = None,
        vector_store=None,
    ) -> None:
        self._settings = settings
        self._vector_store = vector_store or MilvusVectorStore(settings)
        self._embeddings = (
            embeddings or create_embeddings(settings)
        )
        self._reranker = (
            reranker
            or QwenReranker(
                endpoint=settings.rerank_url,
                api_key=settings.chat_api_key,
                model=settings.rerank_model,
                timeout_seconds=(
                    settings.rerank_timeout_seconds
                ),
            )
        )

    def refresh_evidence(self, documents, *, scope, session):
        expected = {doc.metadata["chunk_id"]: doc.metadata for doc in documents
                    if scope.contains(doc.metadata.get("tenant_id"), doc.metadata.get("knowledge_base_id"))
                    and doc.metadata.get("chunk_id")}
        repository = ChunkRepository(session, self._vector_store)
        reader = (repository.parent_chunks if getattr(getattr(self, '_settings', None), 'rag_parent_context_enabled', False)
                  else repository.current_chunks)
        current = [stored_chunk_to_document(row) for row in reader(scope=scope, expected=expected)]
        original = {doc.metadata.get("chunk_id"): doc.metadata for doc in documents}
        policies = repository.candidate_policies(scope=scope,
            document_ids={UUID(doc.metadata["document_id"]) for doc in current})
        names = {str(key): name for key, name in scope.knowledge_base_names}
        for doc in current:
            for key in RANK_METADATA_KEYS:
                if key in original.get(doc.metadata["chunk_id"], {}):
                    doc.metadata[key] = original[doc.metadata["chunk_id"]][key]
            if doc.metadata["document_id"] in policies:
                doc.metadata["policy"] = policies[doc.metadata["document_id"]]
            doc.metadata["knowledge_base_name"] = names.get(doc.metadata["knowledge_base_id"])
        return current

    def read_evidence(self, anchor, *, scope, session):
        rows = ChunkRepository(session, self._vector_store).neighboring_chunks(scope=scope,
            chunk_id=anchor.metadata["chunk_id"], generation=anchor.metadata["vector_generation"],
            document_id=UUID(anchor.metadata["document_id"]))
        if getattr(getattr(self, '_settings', None), 'rag_parent_context_enabled', False):
            rows = ChunkRepository(session, self._vector_store).parent_chunks(scope=scope,
                expected={row.chunk_id:row.metadata for row in rows})
        return [stored_chunk_to_document(row) for row in rows]

    @audited_scope
    def search(
        self,
        query: str,
        *,
        scope: RetrievalScope,
        session: Session,
    ) -> list[Document]:
        """执行范围内向量、关键词融合和重排序。"""
        query = query.strip()

        if not query:
            raise ValueError("问题不能为空")
        record_scope(query, scope)

        repository = ChunkRepository(session, self._vector_store)

        if not repository.has_ready_chunks(scope=scope):
            for stage in ("vector", "keyword", "fusion", "rerank"):
                record_documents(stage, [])
            return []

        vector_retriever = MilvusRetriever(
            repository=repository,
            embeddings=self._embeddings,
            scope=scope,
            k=FETCH_K,
        )

        hybrid_retriever = create_hybrid_retriever(
            vector_retriever=vector_retriever,
            keyword_retriever=PgBM25Retriever(
                repository=PgKeywordRepository(session), scope=scope, k=FETCH_K,
            ),
            k=FETCH_K,
            fetch_k=FETCH_K,
        )

        def prepare_candidates(question, candidates):
            candidates = [doc for doc in candidates if scope.contains(
                doc.metadata.get("tenant_id"), doc.metadata.get("knowledge_base_id"))]
            candidates = expand_incident_candidates(question, candidates, repository=repository, scope=scope, budget=FETCH_K)
            record_documents("expanded_candidates", candidates)
            ids = set()
            for doc in candidates:
                try:
                    ids.add(UUID(str(doc.metadata.get("document_id"))))
                except ValueError:
                    continue
            policies = repository.candidate_policies(scope=scope, document_ids=ids) if ids else {}
            for doc in candidates:
                policy = policies.get(str(doc.metadata.get("document_id")))
                if policy:
                    doc.metadata["policy"] = policy
            record_documents("policy_candidates_raw", candidates)
            candidates = select_evidence(question, candidates)
            record_documents("policy_candidates", candidates)
            return candidates

        final_k = getattr(self._settings, 'rag_rerank_top_k', FINAL_K)
        retriever = RerankRetriever(
            base_retriever=hybrid_retriever,
            reranker=self._reranker,
            top_n=final_k,
            search_kwargs={
                "k": final_k,
                "fetch_k": FETCH_K,
            },
            candidate_processor=prepare_candidates,
            result_processor=select_evidence,
        )

        documents = retriever.invoke(query)
        record_documents("rerank", documents)
        if getattr(self._settings, 'rag_parent_context_enabled', False):
            original = {doc.metadata['chunk_id']:doc.metadata for doc in documents}
            parents = repository.parent_chunks(scope=scope, expected=original)
            documents = [stored_chunk_to_document(row) for row in parents]
            for doc in documents:
                for key in RANK_METADATA_KEYS:
                    if key in original.get(doc.metadata['chunk_id'],{}):
                        doc.metadata[key]=original[doc.metadata['chunk_id']][key]
            documents = select_evidence(query, documents)
            record_documents('parent_context', documents)
        names = {str(kb_id): name for kb_id, name in scope.knowledge_base_names}
        for document in documents:
            document.metadata["knowledge_base_name"] = names.get(document.metadata.get("knowledge_base_id"))
        return documents
