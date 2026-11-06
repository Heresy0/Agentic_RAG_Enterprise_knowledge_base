"""Isolated HTTP + shared generation harness. All models and Redis are local doubles."""
import json
import re
from types import SimpleNamespace

import httpx
from pydantic import SecretStr
from langchain_core.embeddings import Embeddings
from sqlalchemy import select

from backend.app.api import manufacturing, conversations, qa, feedback
from backend.app.db.business_models import BusinessScopeGrant
from backend.app.knowledge.answer_service import AnswerService
from backend.app.knowledge.chunk_repository import ChunkRepository
from backend.app.knowledge.evidence_coordinator import EvidenceCoordinator
from backend.app.knowledge.evidence_feedback import EvidenceFeedback
from backend.app.knowledge.conversation_service import ConversationAnswerService
from backend.app.knowledge.retrieval_service import RetrievalService
from backend.app.knowledge.keyword_repository import PgKeywordRepository, KeywordSearchHit
from backend.app.knowledge.milvus_store import make_vector_records
from backend.app.manufacturing.answer_context import current_manufacturing_context
from backend.app.manufacturing.contracts import WorkOrderFixtures
from backend.app.integrations.mes import MESConnector
from backend.app.integrations.mes_contracts import MESSourceConfig
from services.mock_mes.app import SourceCredential, create_app
from test_document_upload_api import _set_principal
from test_answer_service import build_service
from test_qa_api import FakeTenantRateLimiter, FakeTenantConcurrencyLimiter
from test_retrieval_service import FakeEmbeddings, FakeReranker
from manufacturing_m2_helpers import FIXTURES, seed_catalog
from fake_vector_store import FakeVectorStore

TEST_SECRET = "manufacturing-m3-local-test-credential-123456789"


class SourceTransport(httpx.AsyncBaseTransport):
    def __init__(self, tenant):
        self.records = {row.work_order_id: {**row.model_dump(mode="json"), "tenant_id": str(tenant)} for row in FIXTURES.records}
        self.tenant = tenant; self.reads = []; self.hook = None; self.failure = None

    async def handle_async_request(self, request):
        if request.url.path.endswith("/context"):
            self.reads.append(str(request.url.path))
            if self.hook: self.hook(self, len(self.reads))
            if self.failure: return self.failure(request)
        fixtures = WorkOrderFixtures.model_validate({**FIXTURES.model_dump(), "tenant_id": self.tenant,
                                                    "records": list(self.records.values())})
        app = create_app(fixtures, (SourceCredential(tenant_id=self.tenant, source_system_id="mock-mes-hc",
            token=SecretStr(TEST_SECRET), plant_codes=frozenset({"HC-P01", "HC-P02"})),))
        return await httpx.ASGITransport(app=app).handle_async_request(request)


class LocalGeneration:
    def __init__(self):
        self.mode = "fact"; self.field = "product_model"; self.hook = None; self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        active = current_manufacturing_context()
        assert active is not None
        if self.hook: self.hook(active)
        body = messages[1].content
        sources = re.findall(r"\[(资料\d+)\]\n(.*?)(?=\n\n\[资料|\n\n工单字段来源|\Z)", body.split("知识库资料：\n", 1)[1], re.S)
        if self.mode == "fact":
            claims = [dict(business_field=self.field, claim_kind="business_fact")]
        elif self.mode == "applicability":
            claims = [dict(claim_kind="applicability")]
        elif self.mode == "comparison":
            claims = [dict(text=f"业务版本{revision}要求连续{number}件首件合格", citations=[next(key for key, content in sources
                if f"业务版本：{revision}；" in content and "角色：instruction" in content)], claim_kind="version_comparison")
                for revision, number in (("1.0", "两"), ("2.0", "三"))]
        else:
            key = next(key for key, content in sources if "角色：instruction" in content)
            claims = [dict(text="须连续两件首件合格", citations=[key], claim_kind="work_requirement")]
        requirements = json.loads(body.split("必答问题清单（用户数据）：\n", 1)[1].split("\n\n", 1)[0])
        return dict(answerable=True, response_type="answer", claims=claims, coverage=[dict(requirement_id=row["id"],
            aspect=row["question"], status="answered", claim_indices=list(range(1, len(claims)+1)),
            missing_information=None, source_quote=None) for row in requirements])


def configure(c, monkeypatch):
    c.app.include_router(manufacturing.router); c.app.include_router(conversations.router)
    c.app.include_router(qa.router); c.app.include_router(feedback.router)
    _set_principal(c, c.editor)
    source = SourceTransport(c.tenant_id)
    config = MESSourceConfig(tenant_id=c.tenant_id, source_system_id="mock-mes-hc", base_url="http://127.0.0.1:8010",
        token=TEST_SECRET, allowed_plants=("HC-P01", "HC-P02"))
    connector = MESConnector(config, transport=source)
    c.app.state.mes_connectors = {c.tenant_id: connector}
    with c.session_factory() as session:
        docs = seed_catalog(session, c.tenant_id, c.knowledge_base_id)
        session.add(BusinessScopeGrant(tenant_id=c.tenant_id, user_id=c.editor.user_id, source_system_id="mock-mes-hc",
            plant_code="HC-P01", permission="view")); session.commit()
    base, *_ = build_service(monkeypatch, model_result={})
    settings = base._settings
    settings.qa_rate_limit_requests = 60; settings.qa_rate_limit_window_seconds = 60
    settings.qa_max_concurrent_requests_per_tenant = 3; settings.qa_concurrency_lease_seconds = 240
    settings.qa_snapshots_enabled = True; settings.rag_parent_context_enabled = False
    settings.agentic_rag_mode = "off"
    store = FakeVectorStore()
    for _, chunk, _ in docs.values(): store.upsert(make_vector_records([chunk], [[1.0]+[0.0]*1023]))
    retrieval = RetrievalService(settings, embeddings=FakeEmbeddings(), reranker=FakeReranker(calls=[]), vector_store=store)
    def keyword(self, query, *, scope, limit=30):
        return [KeywordSearchHit(**{key: getattr(row, key) for key in row.__dataclass_fields__}, score=1.0)
                for row in ChunkRepository(self._session).list_chunks(scope=scope)][:limit]
    monkeypatch.setattr(PgKeywordRepository, "search", keyword)
    generation = LocalGeneration()
    base._manufacturing_llm = generation; base._retrieval_service = retrieval
    base._evidence_coordinator = EvidenceCoordinator(settings, retrieval)
    base._evidence_feedback = EvidenceFeedback(settings, retrieval, base._evidence_coordinator, base._context_builder)
    c.app.state.answer_service = base; c.app.state.settings = settings
    c.app.state.tenant_rate_limiter = FakeTenantRateLimiter(); c.app.state.tenant_concurrency_limiter = FakeTenantConcurrencyLimiter()
    resolver = SimpleNamespace(resolve=lambda question, turns: question)
    c.app.state.conversation_answer_service = ConversationAnswerService(base, resolver)
    return SimpleNamespace(c=c, source=source, connector=connector, generation=generation, docs=docs,
                           answer=base, settings=settings)


def qa_input(h, wo="WO-018", question="这张工单的型号是什么？", conversation_id=None, **extra):
    return {**dict(work_order_id=wo, question=question, knowledge_base_id=str(h.c.knowledge_base_id),
                conversation_id=str(conversation_id) if conversation_id else None), **extra}
