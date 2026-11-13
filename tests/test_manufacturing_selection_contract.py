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
from backend.app.manufacturing.generation_selection import selection_schema, GroundedDraft
from backend.app.knowledge.answer_models import EvidenceFirstAnswerDraft
from backend.app.knowledge.answer_service import AnswerService
from backend.app.knowledge.answer_validation import AnswerValidator
from backend.app.manufacturing.answer_context import manufacturing_answer_context


def payload(*sources):
    return dict(answerable=True, response_type="answer", claims=[dict(source=source) for source in sources],
                coverage=[dict(requirement_id="Q1", aspect="用户所问的字段或原文", status="answered",
                    claim_indices=list(range(1, len(sources) + 1)), missing_information=None)])


def test_legacy_fields_and_unknown_values_still_compile(catalog):
    active, _, _ = evidence(catalog)
    with manufacturing_answer_context(replace(active, facts_only=True)):
        context = ContextBuilder().build([])
        source = next(item for item in context.business_items if item.field_name == "route_revision")
        assert source.value is None
        wire = selection_schema(context).model_validate(payload(source.citation_id))
        compiled = prepare_draft(wire, context)
        assert compiled.claims[0].text == source.content
        assert compiled.claims[0].citations == [source.citation_id]
        assert compiled.claims[0].claim_kind == "business_fact"
        assert AnswerValidator().validate(draft=compiled, context=context, question="路线修订？").valid
        assert prepare_draft(compiled, context) == compiled


def test_legacy_document_selection_keeps_complete_table(catalog):
    active, context, citation = built_source(catalog, TABLE)
    with manufacturing_answer_context(active):
        wire = selection_schema(context).model_validate(payload(citation + "#1"))
        compiled = prepare_draft(wire, context)
        assert compiled.claims[0].text == TABLE
        assert compiled.claims[0].citations == [citation]
        assert compiled.claims[0].claim_kind == "work_requirement"
        assert AnswerValidator().validate(draft=compiled, context=context, question="检验要求？").valid


def test_live_record_selection_derives_role_and_order_binding(catalog):
    active, context, citation = built_source(catalog, "记录尚未签署，不能证明已放行。", "event_record")
    with manufacturing_answer_context(active):
        compiled = prepare_draft(selection_schema(context).model_validate(payload(citation + "#1")), context)
        assert compiled.claims[0].claim_kind == "work_order_evidence"
        assert set(compiled.claims[0].citations) == {citation, "业务1"}
        assert AnswerValidator().validate(draft=compiled, context=context, question="记录能证明放行吗？").valid


@pytest.mark.parametrize("source", ["资料1#1", "业务999", "业务1#1", "password", ""])
def test_field_only_generation_cannot_select_absent_document_or_field(catalog, source):
    active, _, _ = evidence(catalog)
    with manufacturing_answer_context(replace(active, facts_only=True)):
        context = ContextBuilder().build([])
        with pytest.raises(ValidationError):
            selection_schema(context).model_validate(payload(source))


@pytest.mark.parametrize("extra", [dict(text="伪造已放行"), dict(citations=["资料999"]),
    dict(claim_kind="applicability"), dict(business_field="status"), dict(calculation=None)])
def test_live_claim_schema_has_no_parallel_assertion_path(catalog, extra):
    active, _, _ = evidence(catalog)
    with manufacturing_answer_context(replace(active, facts_only=True)):
        context = ContextBuilder().build([])
        data = payload("业务1")
        data["claims"][0].update(extra)
        with pytest.raises(ValidationError):
            selection_schema(context).model_validate(data)


def test_selection_is_rechecked_against_current_source_pool(catalog):
    active, context, citation = built_source(catalog, TABLE)
    with manufacturing_answer_context(active):
        wire = selection_schema(context).model_validate(payload(citation + "#1"))
        with pytest.raises(ValidationError):
            prepare_draft(wire, replace(context, items=[]))


def test_invalid_coverage_is_not_filled_in_by_source_compilation(catalog):
    active, _, _ = evidence(catalog)
    with manufacturing_answer_context(replace(active, facts_only=True)):
        context = ContextBuilder().build([])
        data = payload("业务1")
        data["coverage"][0]["claim_indices"] = [1, 2]
        draft = prepare_draft(selection_schema(context).model_validate(data), context)
        assert not AnswerValidator().validate(draft=draft, context=context, question="工单编号？").valid


def test_ordinary_schema_and_prompt_remain_separate(catalog):
    ordinary = ContextBuilder().build([])
    assert live_generation_schema(ordinary) is EvidenceFirstAnswerDraft
    active, _, _ = evidence(catalog)
    with manufacturing_answer_context(replace(active, facts_only=True)):
        context = ContextBuilder().build([])
        messages = AnswerService._generation_messages(None, "工单编号？", context)
        assert "claims每项用text" in messages[0].content
        assert "citations填写" in messages[0].content
        assert live_generation_schema(context).model_json_schema()["$defs"]["ManufacturingGroundedClaim"]["required"] == ["text"]
        assert set(live_generation_schema(context).model_json_schema()["$defs"]["ManufacturingGroundedClaim"]["properties"]) == {"text", "citations"}


@pytest.mark.parametrize("stage", ["generation", "regeneration", "schema_recovery"])
def test_live_model_paths_use_grounded_schema_and_compile(catalog, monkeypatch, stage):
    from test_answer_service import build_service
    from backend.app.knowledge.answer_repair import AnswerRepair
    from backend.app.knowledge.rag_budget import RagBudget, rag_budget_scope
    from backend.app.manufacturing.generation_selection import GroundedDraft

    active, _, _ = evidence(catalog)
    service, *_ = build_service(monkeypatch, model_result={})
    service._settings.rag_repair_enabled = True
    service._settings.rag_repair_prompt_bytes = 48000
    schemas = []

    def structured(schema, **_):
        schemas.append(schema)
        return SimpleNamespace(invoke=lambda _: schema.model_validate(grounded_payload(context.business_items[1].content, "业务2")))

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
        assert len(schemas) == 1 and issubclass(schemas[0], GroundedDraft)
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


def grounded_payload(text, *citations):
    data = payload("placeholder")
    data["claims"] = [dict(text=text, citations=list(citations))]
    return data


@pytest.mark.parametrize("text,valid", [
    ("开工登记不能证明检验已经合格，仍须检验并经质量签署。", True),
    ("应检验9件并经质量签署。", False),
    ("当前作业对象是HC-Z99。", False),
])
def test_grounded_explanation_keeps_numeric_and_identity_checks(catalog, text, valid):
    active, context, citation = built_source(catalog, "开工记录不是检验合格的证明。须检验3件并由质量人员签署。")
    with manufacturing_answer_context(active):
        draft = prepare_draft(live_generation_schema(context).model_validate(grounded_payload(text, citation)), context)
        result = AnswerValidator().validate(draft=draft, context=context, question="开工是否等于检验合格？")
        assert result.valid is valid, result.errors


def test_grounded_claim_still_requires_current_citation(catalog):
    active, context, _ = built_source(catalog, "必须质量签署。")
    with manufacturing_answer_context(active):
        draft = prepare_draft(grounded_payload("必须质量签署。", "资料999"), context)
        assert not AnswerValidator().validate(draft=draft, context=context, question="签署要求？").valid


def test_notice_excerpt_preserves_notice_role(catalog):
    text = "本次变更将首件数量由2件调整为3件。"
    active, context, citation = built_source(catalog, text, "change_notice")
    with manufacturing_answer_context(active):
        draft = prepare_draft(grounded_payload(text, citation), context)
        assert draft.claims[0].claim_kind is None
        result = AnswerValidator().validate(draft=draft, context=context, question="通知说明了什么？")
        assert result.valid, result.errors


def test_http_grounded_output_retains_three_source_checks(harness, monkeypatch):
    h = harness
    monkeypatch.setattr(h.generation, "invoke", lambda _: grounded_payload("产品型号：HC-A17", "业务2"))
    with TestClient(h.c.app) as client:
        response = ask(client, h, question="产品型号是什么？")
        assert response.status_code == 200, response.text
        assert response.json()["answerable"]
    assert len(h.source.reads) == 3


def test_grounded_repair_prompt_does_not_reintroduce_legacy_selectors(catalog):
    from backend.app.knowledge.output_contract import with_manufacturing_rules
    active, context, citation = built_source(catalog, "开工记录不等于检验合格，仍须质量签署。")
    with manufacturing_answer_context(active):
        draft = prepare_draft(grounded_payload("仍须质量签署。", citation), context)
        prompt = with_manufacturing_rules("仅修复引用，保留文字。", context, draft=draft)
        assert "claims每项用text" in prompt
        assert "source_unit" not in prompt and "business_field" not in prompt


@pytest.mark.parametrize("citation", ["[资料1]", "资料1#1", "已核验选版结论", "资料999", "资料1 全文摘录", '{"id":"资料1"}'])
def test_grounded_live_schema_only_exposes_current_citation_pool(catalog, citation):
    active, context, _ = built_source(catalog, "条款正文。")
    with manufacturing_answer_context(active):
        with pytest.raises(ValidationError):
            live_generation_schema(context).model_validate(grounded_payload("据条款作答。", citation))


@pytest.mark.parametrize("text,supported", [("温度应为85摄氏度。", True), ("部件编号为PART-Z91。", True),
                                            ("部件编号为PART-Z92。", False), ("温度应为86摄氏度。", False)])
def test_grounded_binding_repair_eligibility_keeps_type_and_authorized_pool(catalog, text, supported):
    from backend.app.knowledge.answer_repair import numeric_binding_supported
    active, context, citation = built_source(catalog, "温度应为80摄氏度。")
    second = replace(context.items[0], citation_id="资料9", content="温度应为85摄氏度。部件编号为PART-Z91。")
    context = replace(context, items=[*context.items, second])
    with manufacturing_answer_context(active):
        draft = GroundedDraft.model_validate(grounded_payload(text, citation))
        validation = AnswerValidator().validate(draft=draft, context=context, question="条件是什么？")
        assert not validation.valid
        assert numeric_binding_supported(draft, validation.errors, AnswerValidator(), context, "条件是什么？") is supported
        assert draft.claims[0].citations == [citation]
        if supported:
            rebound = draft.model_copy(deep=True)
            rebound.claims[0].citations = ["资料9"]
            assert AnswerValidator().validate(draft=rebound, context=context, question="条件是什么？").valid


def test_grounded_repair_restores_exact_server_applicability_without_duplicate(catalog):
    from backend.app.knowledge.answer_repair import preserves_facts
    active, rows, _ = evidence(catalog)
    with manufacturing_answer_context(active):
        context = ContextBuilder().build(rows)
        original = prepare_draft(grounded_payload("应按已核验的适用版本作业。", context.items[0].citation_id), context)
        assert original.claims[-1].claim_kind == "applicability"
        data = original.model_dump()
        for claim in data["claims"]:claim.pop("claim_kind", None)
        repaired = prepare_draft(live_generation_schema(context).model_validate(data), context)
        assert len(repaired.claims) == len(original.claims)
        assert repaired.claims[-1].claim_kind == "applicability"
        assert preserves_facts(original, repaired)
        assert repaired.claims[-1].citations == original.claims[-1].citations
        from backend.app.knowledge.answer_repair import preserves_decision_addition
        from backend.app.manufacturing.generation_selection import GroundedClaim
        verdict = repaired.model_copy(deep=True)
        verdict.claims.insert(0, GroundedClaim(text="应按当前适用版本作业。", citations=[context.items[0].citation_id]))
        assert preserves_decision_addition(original, verdict)
        verdict.claims[1].citations = ["资料999"]
        assert not preserves_decision_addition(original, verdict)
