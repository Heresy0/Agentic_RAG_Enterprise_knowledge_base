"""Real layout regressions without models or frozen-score changes."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from test_document_upload_api import api_context
from test_manufacturing_applicability import catalog
from test_manufacturing_m3_validation import evidence
from test_manufacturing_server_bindings import draft_for
from test_manufacturing_m3_api import harness, ask
from backend.app.knowledge.answer_models import AnswerClaim, AnswerDraft
from backend.app.knowledge.answer_service import AnswerService
from backend.app.knowledge.answer_validation import AnswerValidator
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.manufacturing.answer_context import complete_source_statement, manufacturing_answer_context
from backend.app.manufacturing.generation_models import ManufacturingClaim, compile_applicability_bindings
from backend.app.manufacturing.source_statements import statement_units, source_units, duration_question, duration_verdict


TABLE = "项目|标准|工具|前提\n外径|21.90至22.10 mm，包含上下限|MIC-025|记录原始读数\n孔深|10.00至10.20 mm，包含上下限|CAL-150|清洁孔内切屑后测量"
TRIGGERS = "出现下列任一情形时必须重新进行首件确认：\n-新工单开工。\n-更换定位治具。\n-连续停机超过7小时后重新开工。"


@pytest.mark.parametrize("source,quote,valid", [
    ("# 标题\n\n适用于本产品。此前已开工工单按通知执行。", "适用于本产品。此前已开工工单按通知执行。", True),
    ("前一段。\n\n## 适用范围\n\n开工前必须清洁；未经签署不得继续。", "开工前必须清洁；未经签署不得继续", True),
    ("如果治具已更换，\n\n必须重新首件。", "必须重新首件", False),
    ("更换后必须复检；\n未完成复检，不能证明已合格。", "已合格", False),
    ("记录已合格？尚无签署。", "记录已合格", False),
    (TRIGGERS, "连续停机超过7小时后重新开工。", False),
    (TRIGGERS, TRIGGERS, True),
    (TABLE, "孔深|10.00至10.20 mm，包含上下限", False),
    (TABLE, "清洁孔内切屑后测量", False),
    (TABLE, TABLE, True),
    ("恢复前必须同时满足：\n\n1. 检查全部合格。\n2. 质量签署。", "质量签署", False),
])
def test_structural_boundaries_keep_original_conditions(source, quote, valid):
    assert complete_source_statement(quote, [SimpleNamespace(content=source)]) is valid


@pytest.mark.parametrize("extra", [dict(source_unit="")])
def test_source_selector_cannot_carry_model_assertions(extra):
    with pytest.raises(ValidationError):
        ManufacturingClaim.model_validate(dict(claim_kind="work_requirement", source_unit="资料1#1") | extra)


@pytest.mark.parametrize('secondary', ['资料1#1', '资料999#1', '业务2'])
def test_explicit_business_field_is_the_only_source_even_with_a_document_pointer(catalog, secondary):
    active, rows, _ = evidence(catalog)
    with manufacturing_answer_context(replace(active, facts_only=True)):
        built = ContextBuilder().build([])
        draft = draft_for(dict(claim_kind='work_requirement', business_field='product_model',
            source_unit=secondary, text='伪造值HC-OTHER并已放行', citations=['资料999']))
        compiled = compile_applicability_bindings(draft, built)
        fact = next(item for item in active.items if item.field_name=='product_model')
        assert len(compiled.claims)==1
        assert compiled.claims[0].text==fact.content and compiled.claims[0].citations==[fact.citation_id]
        assert compiled.claims[0].source_unit is None and compiled.claims[0].business_field is None
        assert compiled.claims[0].claim_kind=='business_fact'
        assert AnswerValidator().validate(draft=compiled, context=built, question='产品型号是什么？').valid


@pytest.mark.parametrize('hint', ['business_fact', 'applicability', 'work_requirement', 'work_order_evidence'])
def test_source_selection_ignores_display_but_unknown_selector_never_falls_back(catalog, hint):
    from backend.app.knowledge.output_contract import validate_output
    active, built, citation = built_source(catalog, TABLE)
    with manufacturing_answer_context(active):
        original = draft_for(dict(claim_kind="work_requirement", source_unit=citation+'#1',
            text="该工单已完成检验并放行", citations=['资料999','业务999']))
        compiled = compile_applicability_bindings(original, built)
        assert compiled.claims[0].text == TABLE and compiled.claims[0].citations == [citation]
        assert AnswerValidator().validate(draft=compiled, context=built, question='检验规范？').valid
        unknown = draft_for(dict(claim_kind=hint, source_unit='资料999#1', text=TABLE, citations=[citation]))
        unresolved = compile_applicability_bindings(unknown, built)
        assert unresolved.claims[0].text == '' and unresolved.claims[0].citations == []
        result = validate_output(unresolved, validator=AnswerValidator(), context=built, question='检验规范？', coverage=[])
        assert not result.valid and any('条目不存在' in error for error in result.errors)


@pytest.mark.parametrize('selector', [dict(business_field='product_model'), dict(source_unit='资料1#1')])
def test_selector_cannot_also_request_a_calculation(selector):
    calculation=dict(operation='money_sum', inputs=[dict(source='问题', quote='1元', value='1', unit='元'),
        dict(source='资料1', quote='2元', value='2', unit='元')])
    with pytest.raises(ValidationError) as failure:
        ManufacturingClaim.model_validate(dict(claim_kind='work_requirement', calculation=calculation) | selector)
    assert failure.value.errors(include_input=False)[0]['type'] == 'manufacturing_selector_calculation'


@pytest.mark.parametrize('selector', ['业务11', '资料1#0', '资料1#-1', '资料1#1#2'])
def test_malformed_document_selector_is_a_recoverable_schema_error(selector):
    with pytest.raises(ValidationError) as failure:
        ManufacturingClaim(claim_kind='work_requirement', source_unit=selector)
    assert failure.value.errors(include_input=False)[0]['type'] == 'manufacturing_source_unit_format'


def test_document_selector_keeps_arbitrary_registered_citation_names():
    assert ManufacturingClaim(claim_kind='work_requirement', source_unit='manual-A#2').source_unit == 'manual-A#2'


@pytest.mark.parametrize("blank", ["", " ", "\n\t"])
def test_unused_selector_does_not_reject_a_real_field_or_backend_applicability(blank):
    field = ManufacturingClaim(claim_kind="business_fact", business_field="product_model", source_unit=blank)
    selection = ManufacturingClaim(claim_kind="applicability", source_unit=blank)
    assert field.source_unit is selection.source_unit is None
    with pytest.raises(ValidationError):
        ManufacturingClaim(claim_kind="work_requirement", source_unit=blank)


@pytest.mark.parametrize("hint", ["business_fact", "applicability", "work_requirement", "work_order_evidence"])
def test_source_only_selector_role_comes_from_document_not_model_hint(catalog, hint):
    active, built, citation = built_source(catalog, TABLE)
    with manufacturing_answer_context(active):
        compiled = compile_applicability_bindings(draft_for(dict(claim_kind=hint, source_unit=citation + "#1")), built)
        assert compiled.claims[0].claim_kind == "work_requirement"
        assert compiled.claims[0].citations == [citation]
        assert compiled.claims[0].text == TABLE
        assert AnswerValidator().validate(draft=compiled, context=built, question="检验规范是什么？").valid


def built_source(catalog, content, role="instruction"):
    active, rows, _ = evidence(catalog)
    with manufacturing_answer_context(active):
        built = ContextBuilder().build(rows)
    item = next(i for i in built.items if i.document_role == role)
    built = replace(built, items=[replace(i, content=content) if i == item else i for i in built.items])
    return active, built, item.citation_id


def test_table_selector_copies_complete_rows_header_units_and_conditions(catalog):
    active, built, citation = built_source(catalog, "## 检验\n\n" + TABLE + "\n\n零读数也必须记录。")
    with manufacturing_answer_context(active):
        original = draft_for(dict(claim_kind="work_requirement", source_unit=citation + "#1"))
        compiled = compile_applicability_bindings(original, built)
        assert compiled.claims[0].text == TABLE
        assert compiled.claims[0].citations == [citation]
        assert AnswerValidator().validate(draft=compiled, context=built, question="两项尺寸、量具与前提？").valid
        assert compile_applicability_bindings(compiled, built) == compiled
        assert original.claims[0].text == "" and not original.claims[0].citations
        assert set(source_units(built)) >= {citation + "#1", citation + "#2"}


def test_repeated_table_selection_displays_once_without_changing_claim_coverage(catalog):
    active, built, citation = built_source(catalog, TABLE)
    with manufacturing_answer_context(active):
        original = draft_for(*[dict(claim_kind='work_requirement', source_unit=citation + '#1') for _ in range(3)])
        compiled = compile_applicability_bindings(original, built)
        result = AnswerService._render_result(None, draft=compiled, context=built)
        assert result.answer.count(TABLE) == 1
        assert compiled.coverage[0].claim_indices == [1, 2, 3]
        assert len(result.claim_bindings) == 4  # Three original claims plus server applicability.


def test_record_selector_derives_role_and_binds_current_work_order(catalog):
    active, built, citation = built_source(catalog, "## 记录边界\n\n更换前记录不能证明更换后复检已完成。", "event_record")
    with manufacturing_answer_context(active):
        draft = draft_for(dict(claim_kind="work_requirement", source_unit=citation + "#1"))
        compiled = compile_applicability_bindings(draft, built)
        assert compiled.claims[0].claim_kind == "work_order_evidence"
        assert compiled.claims[0].citations == [citation, "业务1"]
        assert AnswerValidator().validate(draft=compiled, context=built, question="能证明复检已完成吗？").valid
        # The old assertion path is still rejected rather than silently retyped.
        assertion = AnswerDraft(answerable=True, claims=[AnswerClaim(claim_kind="work_requirement",
            text=compiled.claims[0].text, citations=[citation])])
        assert not AnswerValidator().validate(draft=assertion, context=built).valid


def test_unknown_selector_is_a_validation_failure_not_a_runtime_exception(catalog):
    active, built, _ = built_source(catalog, TABLE)
    with manufacturing_answer_context(active):
        compiled = compile_applicability_bindings(draft_for(dict(claim_kind="work_requirement", source_unit="资料999#3")), built)
        result = AnswerValidator().validate(draft=compiled, context=built)
        assert not result.valid and any("原文条目" in e for e in result.errors)


@pytest.mark.parametrize("duration,unit,expected", [("7", "小时", "未命中"), ("420", "分钟", "未命中"),
    ("6.99", "小时", "未命中"), ("7.01", "小时", "已命中")])
def test_duration_boundary_is_computed_and_keeps_other_triggers_distinct(catalog, duration, unit, expected):
    active, built, citation = built_source(catalog, TRIGGERS)
    question = f"如果连续停机刚好{duration}{unit}，是否必然触发重新首件？"
    with manufacturing_answer_context(active):
        compiled = compile_applicability_bindings(draft_for(dict(claim_kind="work_requirement", source_unit=citation + "#1")), built)
        assert AnswerValidator().validate(draft=compiled, context=built, question=question).valid
        result = AnswerService._render_result(None, draft=compiled, context=built, question=question)
        assert expected + "“超过7小时”" in result.answer
        assert "其他触发条件仍需分别核对" in result.answer
        assert "不能证明实际复检已完成" in result.answer
        assert TRIGGERS in result.answer
        assert result.claim_bindings[0]["derived_condition"]["origin"] == "server_duration_comparison"


@pytest.mark.parametrize("question", ["连续停机可能为7小时", "连续停机为大约7小时", "不是连续停机刚好7小时",
    "连续停机刚好7小时或者连续停机刚好8小时", "连续停机刚好-7小时"])
def test_ambiguous_or_negated_duration_is_not_a_program_input(question):
    assert duration_question(question) is None


@pytest.mark.parametrize("rule", ["连续停机超过7小时后重新开工。", TRIGGERS.replace("任一", "全部"),
    TRIGGERS + "\n但是经批准无需重新首件。", TRIGGERS.replace("超过7小时", "不超过7小时")])
def test_unrecognized_or_exceptional_rule_cannot_generate_a_duration_verdict(catalog, rule):
    active, built, citation = built_source(catalog, rule)
    with manufacturing_answer_context(active):
        compiled = compile_applicability_bindings(draft_for(dict(claim_kind="work_requirement", source_unit=citation + "#1")), built)
        question = "连续停机刚好7小时是否触发？"
        assert duration_verdict(question, compiled.claims[0], built) is None
        assert not AnswerValidator().validate(draft=compiled, context=built, question=question).valid


def test_grounded_context_keeps_only_citation_ids_and_original_content(catalog):
    active, rows, _ = evidence(catalog)
    with manufacturing_answer_context(active):
        built = ContextBuilder().build(rows)
        assert "[原文条目" not in built.text
        assert "原文条目索引" not in built.text
        assert all("[原文条目" not in item.content for item in built.items)
        assert built.total_characters == len(built.text)


@pytest.mark.parametrize("unknown", [False, True])
def test_http_rejects_legacy_document_selectors_before_delivery(harness, monkeypatch, unknown):
    h = harness
    h.generation.mode = "requirements"
    original = h.generation.invoke
    def generate(messages):
        draft = original(messages)
        citation = draft['claims'][0]['citations'][0]
        draft['claims'] = [dict(claim_kind='work_requirement', source_unit='资料999#1' if unknown else citation + '#1')]
        return draft
    monkeypatch.setattr(h.generation, 'invoke', generate)
    with TestClient(h.c.app) as client:
        response = ask(client, h, question='首件要求是什么？')
    body = response.json()
    assert len(h.generation.calls) == 1
    assert response.status_code == 503, response.text
    assert body['detail']['error_code'] == 'manufacturing_generation_invalid'
    assert 'claim_bindings' not in body and 'answer' not in body
