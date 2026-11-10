"""Rules remain source excerpts; execution claims require work-order records."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from test_document_upload_api import api_context
from test_manufacturing_applicability import catalog
from test_manufacturing_m3_api import harness, ask
from test_manufacturing_m3_validation import evidence
from test_manufacturing_server_bindings import draft_for
from backend.app.knowledge.answer_models import AnswerClaim, AnswerDraft
from backend.app.knowledge.answer_service import AnswerService
from backend.app.knowledge.answer_validation import AnswerValidator
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.manufacturing.answer_context import manufacturing_answer_context
from backend.app.manufacturing.generation_models import compile_applicability_bindings


@pytest.mark.parametrize("text", [
    "连续两件首件已检验合格", "首件连续两件不合格即可通过",
    "该工单首件检验完成，须连续两件合格", "连续两件首件合格",
    "连续两件首件合格，无需检验", "1.0：实际开工在切换前的在制工单，连续两件首件不合格",
])
def test_http_never_accepts_rewritten_rule_as_execution_or_inverted_condition(harness, monkeypatch, text):
    h = harness
    h.generation.mode = "requirements"
    original = h.generation.invoke
    def generate(messages):
        result = original(messages)
        result["claims"][0]["text"] = text
        return result
    monkeypatch.setattr(h.generation, "invoke", generate)
    with TestClient(h.c.app) as client:
        response = ask(client, h, question="首件要求是什么？")
    assert response.status_code == 200, response.text
    body = response.json()
    assert not body["answerable"] and not body["citations"] and not body["business_citations"]
    assert "完整原句" in body["refusal_reason"]
    assert len(h.source.reads) == 3 and len(h.generation.calls) == 1


def test_http_complete_requirement_keeps_precondition_and_clear_label(harness):
    h = harness
    h.generation.mode = "requirements"
    with TestClient(h.c.app) as client:
        body = ask(client, h, question="首件要求是什么？").json()
    assert body["answerable"]
    assert "作业要求：1.0：实际开工在切换前的在制工单，连续两件首件合格" in body["answer"]
    assert len(h.source.reads) == 3 and len(h.generation.calls) == 1


@pytest.mark.parametrize("kind", ["work_requirement", "version_comparison"])
def test_record_cannot_launder_execution_into_rule_even_with_a_guide_citation(catalog, kind):
    active, rows, _ = evidence(catalog, comparison=kind == "version_comparison")
    with manufacturing_answer_context(active):
        built = ContextBuilder().build(rows)
        guide = next(item for item in built.items if item.document_role == "instruction")
        event = next(item for item in built.items if item.document_role == "event_record")
        claim = AnswerClaim(text=event.content, claim_kind=kind, citations=[guide.citation_id, event.citation_id])
        result = AnswerValidator().validate(draft=AnswerDraft(answerable=True, claims=[claim]), context=built)
        assert not result.valid and any("完整原句" in error for error in result.errors)


@pytest.mark.parametrize("text,valid", [
    ("首件已检验合格", False), ("未完成复检，不能证明首件已检验合格", False),
    ("更换夹具后必须复检；未完成复检，不能证明首件已检验合格", True),
    ("更换夹具后必须复检；\n未完成复检，不能证明首件已检验合格。", True),
])
def test_record_keeps_semicolon_condition_and_negation(catalog, text, valid):
    active, rows, _ = evidence(catalog)
    with manufacturing_answer_context(active):
        built = ContextBuilder().build(rows)
        event = next(item for item in built.items if item.document_role == "event_record")
        built = replace(built, items=[replace(item, content="更换夹具后必须复检；未完成复检，不能证明首件已检验合格。")
            if item == event else item for item in built.items])
        draft = draft_for(dict(text=text, citations=[event.citation_id], claim_kind="work_order_evidence"))
        compiled = compile_applicability_bindings(draft, built)
        result = AnswerValidator().validate(draft=compiled, context=built, question="记录边界？")
        assert result.valid is valid
        if valid:
            assert compiled.claims[0].text == text.rstrip("。")
            assert "业务1" in compiled.claims[0].citations


def test_version_labels_come_from_cited_guides_even_when_original_has_no_revision(catalog):
    active, rows, _ = evidence(catalog, comparison=True)
    with manufacturing_answer_context(active):
        built = ContextBuilder().build(rows)
        built = replace(built, items=[replace(item, content=item.content.split("：", 1)[1])
            if item.document_role == "instruction" else item for item in built.items])
        guides = [item for item in built.items if item.document_role == "instruction"]
        draft = AnswerDraft(answerable=True, claims=[AnswerClaim(text=item.content,
            citations=[item.citation_id], claim_kind="version_comparison") for item in guides])
        assert AnswerValidator().validate(draft=draft, context=built).valid
        result = AnswerService._render_result(None, draft=draft, context=built)
        for item in guides:
            assert f"业务版本{item.business_revision}：{item.content.rstrip('。')}" in result.answer
        draft.claims[0].text = "连续两件首件已检验合格"
        assert not AnswerValidator().validate(draft=draft, context=built).valid


@pytest.mark.parametrize("source,text,valid", [
    ("首件已检验合格？尚无签字确认。", "首件已检验合格", False),
    ("首件已检验合格？尚无签字确认。", "首件已检验合格？尚无签字确认", True),
    ("首件连续两件已检验合格。", "首件连续两件已检验合格", True),
])
def test_execution_record_preserves_question_punctuation_and_accepts_confirmed_state(catalog, source, text, valid):
    active, rows, _ = evidence(catalog)
    with manufacturing_answer_context(active):
        built = ContextBuilder().build(rows)
        event = next(item for item in built.items if item.document_role == "event_record")
        built = replace(built, items=[replace(item, content=source) if item == event else item for item in built.items])
        draft = draft_for(dict(text=text, citations=[event.citation_id], claim_kind="work_order_evidence"))
        compiled = compile_applicability_bindings(draft, built)
        assert AnswerValidator().validate(draft=compiled, context=built, question="检验记录？").valid is valid
        if valid:
            result = AnswerService._render_result(None, draft=compiled, context=built)
            assert "工单记录：" + text in result.answer
