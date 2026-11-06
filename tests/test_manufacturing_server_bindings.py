"""Server-owned field rendering and evidence binding over verified source data."""
from dataclasses import replace
from types import SimpleNamespace
from typing import get_args

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from test_document_upload_api import api_context
from test_manufacturing_applicability import catalog
from test_manufacturing_m3_api import harness, ask
from test_manufacturing_m3_validation import evidence
from backend.app.knowledge.answer_validation import AnswerValidator
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.manufacturing.answer_context import FIELD_LABELS, manufacturing_answer_context
from backend.app.manufacturing.generation_models import (
    BusinessField, ManufacturingClaim, ManufacturingGenerationDraft, compile_applicability_bindings,
)


def draft_for(*claims):
    return ManufacturingGenerationDraft.model_validate(dict(answerable=True, response_type="answer",
        claims=claims, coverage=[dict(requirement_id="Q1", aspect="工单信息", status="answered",
            claim_indices=list(range(1, len(claims) + 1)), missing_information=None)]))


@pytest.mark.parametrize("field", get_args(BusinessField))
def test_field_selector_renders_verified_value_and_citation_without_model_prose(catalog, field):
    active, rows, _ = evidence(catalog)
    assert set(get_args(BusinessField)) == set(FIELD_LABELS)
    with manufacturing_answer_context(replace(active, facts_only=True)):
        built = ContextBuilder().build([])
        original = draft_for(dict(claim_kind="business_fact", business_field=field))
        compiled = compile_applicability_bindings(original, built)
        item = next(item for item in built.business_items if item.field_name == field)
        assert compiled.claims[0].text == item.content
        assert compiled.claims[0].citations == [item.citation_id]
        assert compiled.claims[0].business_field is None
        assert AnswerValidator().validate(draft=compiled, context=built, question="工单信息？").valid
        assert original.claims[0].text == "" and original.claims[0].citations == []
        assert compile_applicability_bindings(compiled, built) == compiled


@pytest.mark.parametrize("extra", [
    dict(text="开工日期为2030-01-01"), dict(citations=["资料1"]),
    dict(citations=["业务4"]), dict(claim_kind="work_requirement"),
    dict(business_field="password"),
])
def test_field_selection_rejects_values_foreign_references_and_unknown_fields(extra):
    with pytest.raises(ValidationError):
        ManufacturingClaim.model_validate(dict(claim_kind="business_fact", business_field="actual_started_at") | extra)


def test_empty_text_requires_a_server_owned_selector():
    for kind in ("business_fact", "work_requirement", "version_comparison", "work_order_evidence"):
        with pytest.raises(ValidationError):
            ManufacturingClaim(claim_kind=kind)


def test_mixed_field_and_requirement_keep_coverage_and_single_applicability_binding(catalog):
    active, rows, _ = evidence(catalog)
    with manufacturing_answer_context(active):
        built = ContextBuilder().build(rows)
        guide = next(item.citation_id for item in built.items if item.document_role == "instruction")
        original = draft_for(dict(claim_kind="business_fact", business_field="actual_started_at"),
            dict(claim_kind="work_requirement", text="该工单的首件须连续两件合格", citations=[guide]))
        compiled = compile_applicability_bindings(original, built)
        assert compiled.claims[1].text == "首件须连续两件合格"
        assert compiled.coverage[0].claim_indices == [1, 2]
        assert [claim.claim_kind for claim in compiled.claims] == ["business_fact", "work_requirement", "applicability"]
        assert AnswerValidator().validate(draft=compiled, context=built, question="首件要求和开工日期？").valid
        assert compile_applicability_bindings(compiled, built) == compiled
        assert original.claims[1].text.startswith("该工单")


def test_applicability_selector_is_rendered_and_bound_by_server(catalog):
    active, rows, _ = evidence(catalog)
    with manufacturing_answer_context(active):
        built = ContextBuilder().build(rows)
        compiled = compile_applicability_bindings(draft_for(dict(claim_kind="applicability")), built)
        assert compiled.claims[0].text == active.applicability_text
        assert {"业务1", "业务2", "业务3", "业务4"} <= set(compiled.claims[0].citations)
        assert AnswerValidator().validate(draft=compiled, context=built, question="适用哪个版本？").valid


@pytest.mark.parametrize("text", [
    "该工单的首件须连续三件合格", "工单WO-017的首件须连续两件合格",
    "工单WO-018-OTHER的首件须连续两件合格",
    "该工单已连续两件首件合格", "该工单已检验完成，首件须连续两件合格",
])
def test_subject_normalization_never_authorizes_wrong_quantity_order_or_execution(catalog, text):
    active, rows, _ = evidence(catalog)
    with manufacturing_answer_context(active):
        built = ContextBuilder().build(rows)
        guide = next(item.citation_id for item in built.items if item.document_role == "instruction")
        compiled = compile_applicability_bindings(draft_for(dict(claim_kind="work_requirement", text=text, citations=[guide])), built)
        assert not AnswerValidator().validate(draft=compiled, context=built).valid


def test_legacy_fabricated_business_fact_is_not_rewritten_into_a_valid_field(catalog):
    active, rows, _ = evidence(catalog)
    with manufacturing_answer_context(replace(active, facts_only=True)):
        built = ContextBuilder().build([])
        original = draft_for(dict(claim_kind="business_fact", text="工单已放行", citations=["业务1"]))
        assert compile_applicability_bindings(original, built) == original
        assert not AnswerValidator().validate(draft=original, context=built).valid


@pytest.mark.parametrize("wo,field,expected", [
    ("WO-018", "actual_started_at", "2026-09-28"),
    ("WO-023", "route_revision", None),
    ("WO-019", "change_notice_refs", ["HC-SW-019"]),
])
def test_http_field_selection_preserves_value_precision_and_business_scope(harness, monkeypatch, wo, field, expected):
    h = harness
    original = h.generation.invoke
    def select(messages):
        value = original(messages)
        value["claims"] = [dict(claim_kind="business_fact", business_field=field)]
        return value
    monkeypatch.setattr(h.generation, "invoke", select)
    with TestClient(h.c.app) as client:
        body = ask(client, h, wo=wo, question="工单的来源字段是什么？").json()
    assert body["answerable"], body
    assert body["business_citations"][0]["field_name"] == field
    assert body["business_citations"][0]["value"] == expected
    assert body["business_citations"][0]["work_order_id"] == wo
    assert len(h.source.reads) == 3 and len(h.generation.calls) == 1


def test_http_legacy_exact_field_response_remains_compatible(harness, monkeypatch):
    from backend.app.manufacturing.answer_context import current_manufacturing_context
    h = harness
    original = h.generation.invoke
    def legacy(messages):
        value = original(messages)
        item = next(item for item in current_manufacturing_context().items if item.field_name == "product_model")
        value["claims"] = [dict(claim_kind="business_fact", text=item.content, citations=[item.citation_id])]
        return value
    monkeypatch.setattr(h.generation, "invoke", legacy)
    with TestClient(h.c.app) as client:
        body = ask(client, h).json()
    assert body["answerable"] and body["business_citations"][0]["value"] == "HC-A17"


@pytest.mark.parametrize("stage", ["regeneration", "repair"])
def test_additional_generation_paths_compile_field_selectors(catalog, monkeypatch, stage):
    from test_answer_service import build_service
    from backend.app.knowledge.rag_budget import RagBudget, rag_budget_scope
    from backend.app.knowledge.answer_repair import AnswerRepair
    active, _, _ = evidence(catalog)
    service, *_ = build_service(monkeypatch, model_result={})
    service._settings.rag_repair_enabled = True
    selected = draft_for(dict(claim_kind="business_fact", business_field="actual_started_at"))
    model = SimpleNamespace(invoke=lambda _: selected.model_dump())
    factory = lambda **_: SimpleNamespace(with_structured_output=lambda *a, **kw: model)
    with manufacturing_answer_context(replace(active, facts_only=True)):
        built = ContextBuilder().build([])
        with rag_budget_scope(RagBudget.from_settings(service._settings)) as budget:
            if stage == "regeneration":
                budget.begin_feedback()
                service._regeneration_model_factory = factory
                monkeypatch.setattr(service, "_regeneration_preflight", lambda *a: ([], 0, 128))
                compiled = service._regenerate_draft(question="实际开工日期？", context=built)
            else:
                item = next(item for item in built.business_items if item.field_name == "actual_started_at")
                original = draft_for(dict(claim_kind="business_fact", text=item.content, citations=["不存在的引用"]))
                repair = AnswerRepair(service._settings, model_factory=factory,
                    guard_factory=lambda *a: SimpleNamespace(available=True, require=lambda: None))
                validator = AnswerValidator()
                compiled = repair.repair(original, question="实际开工日期？", context=built, validator=validator,
                    validation=validator.validate(draft=original, context=built, question="实际开工日期？"),
                    scope=active.decision.scope, session=None, refresh_context=lambda: True)
        assert compiled.claims[0].citations == ["业务4"]
        assert AnswerValidator().validate(draft=compiled, context=built, question="实际开工日期？").valid


def test_http_schema_recovery_compiles_selected_field(harness, monkeypatch):
    from langchain_core.exceptions import OutputParserException
    h = harness
    h.settings.rag_repair_enabled = True
    def invalid(**_):
        raise OutputParserException("synthetic malformed draft")
    monkeypatch.setattr(h.answer, "_generate_draft", invalid)
    monkeypatch.setattr(h.answer._answer_repair, "recover_schema", lambda **_: draft_for(
        dict(claim_kind="business_fact", business_field="actual_started_at")))
    with TestClient(h.c.app) as client:
        body = ask(client, h, question="实际开工日期？").json()
    assert body["answerable"], body
    assert body["business_citations"][0]["value"] == "2026-09-28"
    assert len(h.source.reads) == 3


@pytest.mark.parametrize("change,status", [("draft", 200), ("other_order", 200), ("new_notice", 409)])
def test_http_delivery_rechecks_relevant_catalog_without_blocking_unrelated_edits(harness, change, status):
    from copy import deepcopy
    from manufacturing_m2_helpers import add_source, reviewed_update
    from backend.app.db.models import KnowledgeDocument
    from backend.app.manufacturing.metadata import ManufacturingMetadata
    h = harness
    h.generation.mode = "requirements"
    with h.c.session_factory() as session:
        draft, _, _ = add_source(session, tenant=h.c.tenant_id, kb=h.c.knowledge_base_id,
            code="DRAFT", published=False)
        draft_id = draft.id
        session.commit()
    def edit(_):
        with h.c.session_factory() as session:
            if change == "new_notice":
                add_source(session, tenant=h.c.tenant_id, kb=h.c.knowledge_base_id,
                    code="NEW-NOTICE", role="change_notice")
                session.commit()
            elif change == "draft":
                doc = session.get(KnowledgeDocument, draft_id)
                metadata = deepcopy(doc.metadata_json)
                metadata["manufacturing"]["revision"] += 1
                doc.metadata_json = metadata
                session.commit()
            else:
                doc = session.get(KnowledgeDocument, h.docs["event17"][0].id)
                reviewed_update(session, doc, ManufacturingMetadata.model_validate({
                    **h.docs["event17"][2].model_dump(), "archived": True}))
    h.generation.hook = edit
    with TestClient(h.c.app) as client:
        response = ask(client, h, question="首件要求是什么？")
    assert response.status_code == status, response.text
    if status == 200:
        assert response.json()["answerable"]
    else:
        assert response.json()["detail"]["error_code"] == "manufacturing_scope_changed"
    assert len(h.source.reads) == 3 and len(h.generation.calls) == 1
