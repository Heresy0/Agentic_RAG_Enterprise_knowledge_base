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
                      "rrf_rank", "rrf_score", "model_rank", "final_rank", "manufacturing_required")


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
        scope.require_current_manufacturing(session)
        expected = {doc.metadata["chunk_id"]: doc.metadata for doc in documents
                    if scope.contains_document(doc.metadata)
                    and doc.metadata.get("chunk_id")}
        repository = ChunkRepository(session, self._vector_store)
        reader = (repository.parent_chunks if getattr(getattr(self, '_settings', None), 'rag_parent_context_enabled', False)
                  else repository.current_chunks)
        current = [stored_chunk_to_document(row) for row in reader(scope=scope, expected=expected)]
        if scope.manufacturing is not None:
            mandatory = [stored_chunk_to_document(row) for row in repository.manufacturing_evidence(scope=scope,
                expand_parents=getattr(getattr(self, '_settings', None), 'rag_parent_context_enabled', False))]
            ids = {doc.metadata["chunk_id"] for doc in mandatory}
            current = [*mandatory, *(doc for doc in current if doc.metadata["chunk_id"] not in ids)][:32]
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
        scope.require_current_manufacturing(session)
        if not scope.contains_document(anchor.metadata):
            return []
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
        scope.require_current_manufacturing(session)

        repository = ChunkRepository(session, self._vector_store)
        mandatory = ([stored_chunk_to_document(row) for row in repository.manufacturing_evidence(scope=scope,
                      expand_parents=getattr(self._settings, 'rag_parent_context_enabled', False))]
                     if scope.manufacturing is not None else [])
        if scope.manufacturing is not None:
            record_documents("manufacturing_rule_evidence", mandatory)

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
            candidates = [doc for doc in candidates if scope.contains_document(doc.metadata)]
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
            candidates = select_evidence(question, candidates, scope=scope)
            record_documents("policy_candidates", candidates)
            return candidates

        def finalize_candidates(question, documents):
            documents = select_evidence(question, documents, scope=scope)
            if getattr(self._settings, 'rag_parent_context_enabled', False):
                # Parent resolution already deduplicates within the current
                # authorized generation. Do it before spending the top-k slots.
                original = {doc.metadata['chunk_id']: doc.metadata for doc in documents}
                parents = repository.parent_chunks(scope=scope, expected=original)
                documents = [stored_chunk_to_document(row) for row in parents]
                for doc in documents:
                    for key in RANK_METADATA_KEYS:
                        if key in original.get(doc.metadata['chunk_id'], {}):
                            doc.metadata[key] = original[doc.metadata['chunk_id']][key]
                documents = select_evidence(question, documents, scope=scope)
            return documents

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
            result_processor=finalize_candidates,
        )

        documents = retriever.invoke(query)
        if scope.manufacturing is not None:
            # SQL deciding evidence is mandatory, not a competitor in top-k.
            ids = {doc.metadata["chunk_id"] for doc in mandatory}
            documents = [*mandatory, *(doc for doc in documents if doc.metadata["chunk_id"] not in ids)][:32]
            documents = select_evidence(query, documents, scope=scope)
        record_documents("rerank", documents)
        if getattr(self._settings, 'rag_parent_context_enabled', False):
            record_documents('parent_context', documents)
        names = {str(kb_id): name for kb_id, name in scope.knowledge_base_names}
        for document in documents:
            document.metadata["knowledge_base_name"] = names.get(document.metadata.get("knowledge_base_id"))
        if scope.manufacturing is not None:
            # Reranking/parent expansion must not erase a deciding source, and
            # catalog changes during a model/network call invalidate selection.
            documents = self.refresh_evidence(documents, scope=scope, session=session)
        return documents
