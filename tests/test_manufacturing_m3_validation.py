"""Two source namespaces must never legitimize each other's unsupported claims."""
from dataclasses import replace

import pytest

from test_document_upload_api import api_context
from test_manufacturing_applicability import catalog
from manufacturing_m2_helpers import context, scopes
from backend.app.knowledge.answer_models import AnswerDraft, AnswerClaim
from backend.app.knowledge.answer_validation import AnswerValidator
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.knowledge.chunk_repository import ChunkRepository
from backend.app.knowledge.milvus_retriever import stored_chunk_to_document
from backend.app.manufacturing.answer_context import ManufacturingAnswerContext, manufacturing_answer_context
from backend.app.manufacturing.applicability import ApplicabilityResolver


def evidence(catalog, *, comparison=False):
    c, session, docs = catalog
    scope, business = scopes(c.tenant_id, c.knowledge_base_id, c.editor.user_id)
    source = context(tenant_id=c.tenant_id)
    decision = ApplicabilityResolver().resolve(context=source, scope=scope, business_scope=business,
        session=session, include_other_revisions=comparison)
    active = ManufacturingAnswerContext(source, decision, comparison=comparison)
    rows = ChunkRepository(session).list_chunks(scope=decision.scope)
    return active, [stored_chunk_to_document(row) for row in rows], docs


@pytest.mark.parametrize("kind", ["wrong_field", "extra_claim", "mixed_requirement", "business_only_requirement", "missing_kind", "question_number"])
def test_business_values_never_exempt_unsupported_process_facts_or_identity(catalog, kind):
    active, rows, docs = evidence(catalog)
    with manufacturing_answer_context(active):
        built = ContextBuilder().build(rows)
        guide = next(item.citation_id for item in built.items if item.document_id == str(docs["old"][0].id))
        if kind == "wrong_field": claim = AnswerClaim(text=active.items[1].content, citations=["业务4"], claim_kind="business_fact")
        elif kind == "extra_claim": claim = AnswerClaim(text=active.items[1].content+"，已经放行", citations=["业务2"], claim_kind="business_fact")
        elif kind == "mixed_requirement": claim = AnswerClaim(text="须连续两件首件合格", citations=[guide, "业务1"], claim_kind="work_requirement")
        elif kind == "business_only_requirement": claim = AnswerClaim(text="首件须检验18件", citations=["业务1"], claim_kind="work_requirement")
        elif kind == "missing_kind": claim = AnswerClaim(text="须连续两件首件合格", citations=[guide])
        else: claim = AnswerClaim(text="首件须检查99件", citations=[guide], claim_kind="work_requirement")
        validation = AnswerValidator().validate(draft=AnswerDraft(answerable=True, claims=[claim]), context=built,
            question="如果首件检验99件是否正确？")
        assert not validation.valid


@pytest.mark.parametrize("omission", ["business", "rule", "canonical"])
def test_applicability_requires_exact_server_conclusion_and_both_source_types(catalog, omission):
    active, rows, docs = evidence(catalog)
    with manufacturing_answer_context(active):
        built = ContextBuilder().build(rows)
        citations = [item.citation_id for item in built.items if item.document_role in ("instruction", "change_notice")]
        citations += ["业务1", "业务2", "业务3", "业务4"]
        if omission == "business": citations.remove("业务4")
        if omission == "rule": citations = [key for key in citations if key != next(item.citation_id for item in built.items if item.document_role == "change_notice")]
        text = active.applicability_text + ("并已放行" if omission == "canonical" else "")
        validation = AnswerValidator().validate(draft=AnswerDraft(answerable=True,
            claims=[AnswerClaim(text=text, citations=citations, claim_kind="applicability")]), context=built)
        assert not validation.valid


def test_comparison_reference_cannot_authorize_current_work_order_requirement(catalog):
    active, rows, docs = evidence(catalog, comparison=True)
    with manufacturing_answer_context(active):
        built = ContextBuilder().build(rows)
        new = next(item.citation_id for item in built.items if item.document_id == str(docs["new"][0].id))
        validation = AnswerValidator().validate(draft=AnswerDraft(answerable=True,
            claims=[AnswerClaim(text="业务版本2.0须连续三件首件合格", citations=[new], claim_kind="work_requirement")]), context=built)
        assert not validation.valid
        assert any("非适用指导书" in error for error in validation.errors)


def test_missing_deciding_source_or_budget_never_reaches_generation_context(catalog):
    active, rows, docs = evidence(catalog)
    without_ecn = [row for row in rows if row.metadata["document_id"] != str(docs["ecn"][0].id)]
    with manufacturing_answer_context(active):
        assert ContextBuilder().build(without_ecn).incomplete_reason == "manufacturing_required_evidence_missing"
        assert ContextBuilder(max_characters=30).build(rows).incomplete_reason


def test_business_context_is_request_local_and_unknown_route_is_preserved(catalog):
    active, rows, docs = evidence(catalog)
    with manufacturing_answer_context(active):
        built = ContextBuilder().build([])
        item = next(item for item in built.business_items if item.field_name == "route_revision")
        assert item.value is None and item.content.endswith("未知")
        assert len(built.text) < 4000
    assert ContextBuilder().build([]).business_items == ()


@pytest.mark.parametrize("text,valid", [("WO-018于9月28日开工，10月2日报工", True), ("10月2日报工", False), ("WO-018已放行", False)])
def test_work_order_record_requires_whole_original_sentence_and_business_identity(catalog, text, valid):
    active, rows, docs = evidence(catalog)
    with manufacturing_answer_context(active):
        built = ContextBuilder().build(rows)
        event = next(item.citation_id for item in built.items if item.document_id == str(docs["event18"][0].id))
        draft = AnswerDraft(answerable=True, claims=[AnswerClaim(text=text, citations=[event, "业务1"], claim_kind="work_order_evidence")])
        assert AnswerValidator().validate(draft=draft, context=built).valid is valid


def test_denied_business_guard_prevents_every_structured_model_attempt(catalog):
    from backend.app.manufacturing.answer_context import manufacturing_model_guard
    from backend.app.model_usage import invoke_structured, usage_context
    from backend.app.security.business_scope import BusinessAuthorizationDenied
    active, _, _ = evidence(catalog)
    class Model:
        def invoke(self, _): pytest.fail("model called after business revocation")
    def denied(): raise BusinessAuthorizationDenied()
    with usage_context(scope=active.decision.scope) as usage, manufacturing_model_guard(denied):
        with pytest.raises(BusinessAuthorizationDenied): invoke_structured(Model(), [], stage="answer_repair", model_name="test")
        assert usage.events == []


def test_business_reference_format_can_be_repaired_without_a_model_call(catalog):
    from types import SimpleNamespace
    from backend.app.knowledge.answer_models import GenerationAnswerDraft
    from backend.app.knowledge.answer_repair import AnswerRepair
    active, rows, _ = evidence(catalog)
    with manufacturing_answer_context(active):
        built = ContextBuilder().build(rows)
        draft = GenerationAnswerDraft.model_validate(dict(answerable=True, response_type="answer",
            claims=[dict(text=active.items[1].content, citations=["[业务2]"], claim_kind="business_fact")],
            coverage=[dict(requirement_id="Q1", aspect="产品型号", status="answered", claim_indices=[1], missing_information=None)]))
        validator = AnswerValidator(); validation = validator.validate(draft=draft, context=built, question="产品型号是什么？")
        repaired = AnswerRepair(SimpleNamespace(rag_repair_enabled=True)).repair(draft, question="产品型号是什么？",
            context=built, validator=validator, validation=validation, scope=active.decision.scope, session=None,
            refresh_context=lambda: True)
        assert repaired.claims[0].citations == ["业务2"] and draft.claims[0].citations == ["[业务2]"]


def test_document_rule_cannot_replace_a_missing_work_order_field(catalog):
    active, rows, docs = evidence(catalog)
    with manufacturing_answer_context(replace(active, facts_only=True)):
        built = ContextBuilder().build(rows)
        guide = next(item.citation_id for item in built.items if item.document_role == "instruction")
        draft = AnswerDraft(answerable=True, claims=[AnswerClaim(text="须连续两件首件合格", citations=[guide], claim_kind="work_requirement")])
        assert not AnswerValidator().validate(draft=draft, context=built).valid


def test_manufacturing_kind_is_required_only_in_the_manufacturing_generation_schema():
    from backend.app.knowledge.answer_models import EvidenceFirstAnswerDraft
    from backend.app.manufacturing.generation_models import ManufacturingGenerationDraft
    ordinary = EvidenceFirstAnswerDraft.model_json_schema()["$defs"]["AnswerClaim"]
    manufacturing = ManufacturingGenerationDraft.model_json_schema()["$defs"]["ManufacturingClaim"]
    assert "claim_kind" not in ordinary["properties"]
    assert "claim_kind" in manufacturing["required"]


@pytest.mark.parametrize("quantity,valid", [("两", False), ("2", False), ("三", True), ("3", True)])
def test_revision_number_cannot_support_the_wrong_first_piece_quantity(catalog, quantity, valid):
    c, session, docs = catalog
    scope, business = scopes(c.tenant_id, c.knowledge_base_id)
    source = context("WO-017", tenant_id=c.tenant_id)
    decision = ApplicabilityResolver().resolve(context=source, scope=scope, business_scope=business, session=session)
    with manufacturing_answer_context(ManufacturingAnswerContext(source, decision)):
        built = ContextBuilder().build([stored_chunk_to_document(row) for row in ChunkRepository(session).list_chunks(scope=decision.scope)])
        guide = next(item.citation_id for item in built.items if item.document_role == "instruction")
        claim = AnswerClaim(text=f"首件须连续{quantity}件合格", citations=[guide], claim_kind="work_requirement")
        assert AnswerValidator().validate(draft=AnswerDraft(answerable=True, claims=[claim]), context=built).valid is valid
