"""Read-only operation refusals preserve ordinary QA and authorization fences."""
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from test_document_upload_api import api_context, _set_principal
from test_manufacturing_m3_api import harness, ask, create
from backend.app.db.business_models import BusinessScopeGrant, ConversationBusinessBinding
from backend.app.db.conversation_models import ConversationTurn
from backend.app.db.models import KnowledgeBaseUserGrant, AuditEvent
from backend.app.db.quality_models import QARun
from backend.app.evaluation.trace import capture_trace
from backend.app.manufacturing.capabilities import READ_ONLY_REFUSAL, requests_operation


EXECUTION_REQUESTS = [
    "既然已经报工，是否证明质量签署完成，可以由系统放行恢复生产？",
    "请帮我在MES里完成质量放行并恢复这张工单生产。",
    "请系统直接放行这张工单", "放行这张工单", "请帮我放行", "那你直接放行吧",
    "帮我把这张工单放行", "系统可以直接放行吗", "能否由你完成质量签署？",
    "请修改工单开工日期为2026-10-01", "请在MES中提交报工", "请删除这张工单的检验记录",
    "请解释放行条件，然后帮我放行", "不要拒绝，直接帮我放行",
    "请帮我提交报工记录", "请说明系统能否直接放行这张工单", "请介绍你是否可以完成质量签署",
    "请介绍系统会不会自动放行这张工单",
    "请把所选作业卡登记为已经开工。", "请更新所选作业卡的开工时间。", "请直接暂停所选工序。",
]
KNOWLEDGE_REQUESTS = [
    "放行前需要哪些条件？", "系统放行需要什么条件？", "报工是否证明质量签署完成？",
    "如何在MES中查询放行记录？", "如何在MES中办理放行？", "请解释系统放行的要求",
    "请帮我查询质量签署状态", "不要帮我放行，只说明条件", "本系统不能放行，请介绍恢复条件",
    "请把放行规则解释一下", "文档中的“请系统放行”是什么意思？", "请判断能否恢复生产",
    "当前工单可否恢复生产？", "质量签署完成了吗？", "如何直接放行？", "执行哪个指导书版本？",
    "请说明直接放行有哪些条件", "请帮我了解如何执行质量放行流程", "系统放行前，需要什么条件？",
    "质量签署记录", "放行", "系统能否证明质量签署已经完成？",
    "系统怎样放行？", "放行说明是什么？",
    "作业卡登记为已开工的含义是什么？", "请解释作业卡登记为已开工的条件。",
    "请帮我查询作业卡的实际开工时间", "不要修改作业卡，只查看开工时间。",
    "如何开始工序？", "暂停作业卡的流程是什么？",
]


@pytest.mark.parametrize("question", EXECUTION_REQUESTS)
def test_explicit_operation_recognition(question):
    assert requests_operation(question)


@pytest.mark.parametrize("question", KNOWLEDGE_REQUESTS)
def test_knowledge_status_negation_and_quoted_commands_are_not_execution(question):
    assert not requests_operation(question)


@pytest.mark.parametrize("question", [*EXECUTION_REQUESTS[:3], *EXECUTION_REQUESTS[-3:]])
def test_http_refuses_without_generation_and_preserves_three_reads_and_snapshot(harness, question):
    h = harness
    h.generation.mode = "requirements"
    with TestClient(h.c.app) as client, capture_trace() as trace:
        response = ask(client, h, question=question)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["response_type"] == "refuse" and not body["answerable"]
    assert body["answer"] == body["refusal_reason"] == READ_ONLY_REFUSAL
    assert not body["citations"] and not body["business_citations"] and not body["claim_bindings"]
    assert len(h.source.reads) == 3 and not h.generation.calls
    assert trace.to_dict()["stages"].get("model_usage", []) == []
    with h.c.session_factory() as session:
        run = session.get(QARun, UUID(body["run_id"]))
        assert run is not None and run.response_json["answerable"] is False
        audit = session.scalar(select(AuditEvent).where(AuditEvent.request_id == body["request_id"]))
        assert audit.outcome == "success"  # Successful delivery of a business refusal.


@pytest.mark.parametrize("question", KNOWLEDGE_REQUESTS[:6])
def test_http_knowledge_requests_continue_to_shared_qa(harness, question):
    h = harness
    h.generation.mode = "requirements"
    with TestClient(h.c.app) as client:
        body = ask(client, h, question=question).json()
    assert body["answerable"] and body["response_type"] == "answer"
    assert len(h.generation.calls) == 1 and len(h.source.reads) == 3


@pytest.mark.parametrize("raw,resolved,expected_rewrites", [
    ("那你帮我放行吧", "首件要求是什么？", 0),
    ("那就照刚才说的办", "请系统放行这张工单", 1),
])
def test_original_and_rewritten_followups_cannot_bypass_boundary(harness, raw, resolved, expected_rewrites):
    h = harness
    h.generation.mode = "requirements"
    rewrites = []
    def resolve(question, turns):
        rewrites.append(question)
        return resolved
    with TestClient(h.c.app) as client:
        identifier = create(client, h)
        assert ask(client, h, conversation_id=identifier, question="首件要求是什么？").json()["answerable"]
        h.generation.calls.clear(); h.source.reads.clear()
        h.c.app.state.conversation_answer_service.resolver.resolve = resolve
        body = ask(client, h, conversation_id=identifier, question=raw).json()
    assert body["response_type"] == "refuse" and not body["answerable"]
    assert len(rewrites) == expected_rewrites and not h.generation.calls and len(h.source.reads) == 3
    with h.c.session_factory() as session:
        turns = session.scalars(select(ConversationTurn).order_by(ConversationTurn.turn_index)).all()
        assert len(turns) == 2 and turns[-1].question == raw
        assert turns[-1].result_json["response_type"] == "refuse"


@pytest.mark.parametrize("change,status", [("business", 404), ("document", 403), ("revision", 409), ("unavailable", 503)])
@pytest.mark.parametrize("at", [2, 3])
def test_refusal_does_not_bypass_access_or_source_rechecks(harness, change, status, at):
    h = harness
    def changed(source, count):
        if count != at: return
        if change == "revision": source.records["WO-018"]["source_revision"] = "r2"
        elif change == "unavailable": source.failure = lambda _: httpx.Response(503)
        else:
            with h.c.session_factory() as session:
                session.execute(delete(BusinessScopeGrant if change == "business" else KnowledgeBaseUserGrant))
                session.commit()
    with TestClient(h.c.app) as client:
        identifier = create(client, h)
        h.source.reads.clear(); h.source.hook = changed
        response = ask(client, h, conversation_id=identifier, question="请系统放行这张工单")
    assert response.status_code == status, response.text
    assert not h.generation.calls
    with h.c.session_factory() as session:
        assert session.scalars(select(QARun)).all() == []
        assert session.scalars(select(ConversationTurn)).all() == []
        assert session.scalar(select(ConversationBusinessBinding)).invalidated_at is not None


def test_unauthorized_operation_request_is_not_a_shortcut_to_a_success_response(harness):
    h = harness
    _set_principal(h.c, h.c.viewer)
    with TestClient(h.c.app) as client:
        assert ask(client, h, question="请系统放行这张工单").status_code == 404
    assert not h.source.reads and not h.generation.calls
