"""Live selections compile to the unchanged source-verified internal contract."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from test_document_upload_api import api_context
from test_manufacturing_applicability import catalog
from test_manufacturing_m3_validation import evidence
from test_manufacturing_source_units import built_source, TABLE
from test_manufacturing_m3_api import harness, ask
from fastapi.testclient import TestClient
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.knowledge.output_contract import live_generation_schema, prepare_draft
from backend.app.knowledge.answer_models import EvidenceFirstAnswerDraft
from backend.app.knowledge.answer_service import AnswerService
from backend.app.knowledge.answer_validation import AnswerValidator
from backend.app.manufacturing.answer_context import manufacturing_answer_context


def payload(*sources):
    return dict(answerable=True, response_type="answer", claims=[dict(source=source) for source in sources],
                coverage=[dict(requirement_id="Q1", aspect="用户所问的字段或原文", status="answered",
                    claim_indices=list(range(1, len(sources) + 1)), missing_information=None)])


def test_live_fields_and_unknown_values_compile_without_model_prose(catalog):
    active, _, _ = evidence(catalog)
    with manufacturing_answer_context(replace(active, facts_only=True)):
        context = ContextBuilder().build([])
        source = next(item for item in context.business_items if item.field_name == "route_revision")
        assert source.value is None
        wire = live_generation_schema(context).model_validate(payload(source.citation_id))
        compiled = prepare_draft(wire, context)
        assert compiled.claims[0].text == source.content
        assert compiled.claims[0].citations == [source.citation_id]
        assert compiled.claims[0].claim_kind == "business_fact"
        assert AnswerValidator().validate(draft=compiled, context=context, question="路线修订？").valid
        assert prepare_draft(compiled, context) == compiled


def test_live_document_selection_keeps_complete_table(catalog):
    active, context, citation = built_source(catalog, TABLE)
    with manufacturing_answer_context(active):
        wire = live_generation_schema(context).model_validate(payload(citation + "#1"))
        compiled = prepare_draft(wire, context)
        assert compiled.claims[0].text == TABLE
        assert compiled.claims[0].citations == [citation]
        assert compiled.claims[0].claim_kind == "work_requirement"
        assert AnswerValidator().validate(draft=compiled, context=context, question="检验要求？").valid


def test_live_record_selection_derives_role_and_order_binding(catalog):
    active, context, citation = built_source(catalog, "记录尚未签署，不能证明已放行。", "event_record")
    with manufacturing_answer_context(active):
        compiled = prepare_draft(live_generation_schema(context).model_validate(payload(citation + "#1")), context)
        assert compiled.claims[0].claim_kind == "work_order_evidence"
        assert set(compiled.claims[0].citations) == {citation, "业务1"}
        assert AnswerValidator().validate(draft=compiled, context=context, question="记录能证明放行吗？").valid


@pytest.mark.parametrize("source", ["资料1#1", "业务999", "业务1#1", "password", ""])
def test_field_only_generation_cannot_select_absent_document_or_field(catalog, source):
    active, _, _ = evidence(catalog)
    with manufacturing_answer_context(replace(active, facts_only=True)):
        context = ContextBuilder().build([])
        with pytest.raises(ValidationError):
            live_generation_schema(context).model_validate(payload(source))


@pytest.mark.parametrize("extra", [dict(text="伪造已放行"), dict(citations=["资料999"]),
    dict(claim_kind="applicability"), dict(business_field="status"), dict(calculation=None)])
def test_live_claim_schema_has_no_parallel_assertion_path(catalog, extra):
    active, _, _ = evidence(catalog)
    with manufacturing_answer_context(replace(active, facts_only=True)):
        context = ContextBuilder().build([])
        data = payload("业务1")
        data["claims"][0].update(extra)
        with pytest.raises(ValidationError):
            live_generation_schema(context).model_validate(data)


def test_selection_is_rechecked_against_current_source_pool(catalog):
    active, context, citation = built_source(catalog, TABLE)
    with manufacturing_answer_context(active):
        wire = live_generation_schema(context).model_validate(payload(citation + "#1"))
        with pytest.raises(ValidationError):
            prepare_draft(wire, replace(context, items=[]))


def test_invalid_coverage_is_not_filled_in_by_source_compilation(catalog):
    active, _, _ = evidence(catalog)
    with manufacturing_answer_context(replace(active, facts_only=True)):
        context = ContextBuilder().build([])
        data = payload("业务1")
        data["coverage"][0]["claim_indices"] = [1, 2]
        draft = prepare_draft(live_generation_schema(context).model_validate(data), context)
        assert not AnswerValidator().validate(draft=draft, context=context, question="工单编号？").valid


def test_ordinary_schema_and_prompt_remain_separate(catalog):
    ordinary = ContextBuilder().build([])
    assert live_generation_schema(ordinary) is EvidenceFirstAnswerDraft
    active, _, _ = evidence(catalog)
    with manufacturing_answer_context(replace(active, facts_only=True)):
        context = ContextBuilder().build([])
        messages = AnswerService._generation_messages(None, "工单编号？", context)
        assert "claims每项只有source" in messages[0].content
        assert '"citations"' not in messages[0].content
        assert live_generation_schema(context).model_json_schema()["$defs"]["ManufacturingSourceSelection"]["required"] == ["source"]


@pytest.mark.parametrize("stage", ["generation", "regeneration", "schema_recovery"])
def test_live_model_paths_use_selection_schema_and_compile(catalog, monkeypatch, stage):
    from test_answer_service import build_service
    from backend.app.knowledge.answer_repair import AnswerRepair
    from backend.app.knowledge.rag_budget import RagBudget, rag_budget_scope
    from backend.app.manufacturing.generation_selection import SelectionDraft

    active, _, _ = evidence(catalog)
    service, *_ = build_service(monkeypatch, model_result={})
    service._settings.rag_repair_enabled = True
    service._settings.rag_repair_prompt_bytes = 48000
    schemas = []

    def structured(schema, **_):
        schemas.append(schema)
        return SimpleNamespace(invoke=lambda _: schema.model_validate(payload("业务2")))

    factory = lambda **_: SimpleNamespace(with_structured_output=structured)
    service._base_llm = factory()
    service._regeneration_model_factory = factory
    with manufacturing_answer_context(replace(active, facts_only=True)):
        context = ContextBuilder().build([])
        with rag_budget_scope(RagBudget.from_settings(service._settings)) as budget:
            if stage == "generation":
                draft = service._generate_draft(question="产品型号？", context=context)
            elif stage == "regeneration":
                budget.begin_feedback()
                draft = service._regenerate_draft(question="产品型号？", context=context)
            else:
                repair = AnswerRepair(service._settings, model_factory=factory,
                    guard_factory=lambda *a: SimpleNamespace(available=True, require=lambda: None))
                draft = repair.recover_schema(messages=service._generation_messages("产品型号？", context),
                    context=context, scope=active.decision.scope, session=None, refresh_context=lambda: True)
        assert len(schemas) == 1 and issubclass(schemas[0], SelectionDraft)
        assert draft.claims[0].claim_kind == "business_fact"
        assert draft.claims[0].citations == ["业务2"]
        assert AnswerValidator().validate(draft=draft, context=context, question="产品型号？").valid


def test_raw_selection_payload_is_not_mistaken_for_a_legacy_assertion(catalog):
    active, _, _ = evidence(catalog)
    with manufacturing_answer_context(replace(active, facts_only=True)):
        context = ContextBuilder().build([])
        assert prepare_draft(payload("业务2"), context).claims[0].citations == ["业务2"]


def test_http_source_only_output_retains_three_source_checks(harness, monkeypatch):
    h = harness
    calls = []
    def select(messages):
        calls.append(messages)
        return payload("业务2")
    monkeypatch.setattr(h.generation, "invoke", select)
    with TestClient(h.c.app) as client:
        response = ask(client, h, question="产品型号是什么？")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["answerable"] and body["business_citations"][0]["value"] == "HC-A17"
        assert body["snapshot_status"] == "captured"
    assert len(calls) == 1 and len(h.source.reads) == 3
