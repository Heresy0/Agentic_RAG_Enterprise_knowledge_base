"""Reference labels must neither cause refusals nor prove business quantities."""
import pytest

from test_answer_validation import make_context, make_answerable_draft, validate
from backend.app.knowledge.answer_validation import without_bound_citation_labels


@pytest.mark.parametrize("label", ["资料1", "[资料1]", "【资料1】"])
def test_bound_reference_in_prose_is_not_a_business_number(label):
    context = make_context("末箱核对尚未登记，不能确认检查全部完成。")
    assert validate(make_answerable_draft(f"根据{label}，末箱核对尚未登记，不能确认检查全部完成。"), context).valid


def test_v4_042_reference_numbers_are_not_quantities():
    context = make_context("缺失项保持待复核。", "无关资料。", "缺失标识需待确认。", "未登记末箱核对结果。")
    assert validate(make_answerable_draft("根据资料4，未登记末箱核对结果。", ["资料4"]), context).valid
    assert validate(make_answerable_draft("根据资料1和资料3，缺失项保持待复核或待确认。", ["资料1", "资料3"]), context).valid


@pytest.mark.parametrize("text", ["根据资料1，需要检查1件。", "根据资料1，需要检查4件。",
    "根据资料1，2026-11-13已放行。", "根据资料1，尺寸为0.4mm。",
    "根据资料2，检查已完成。", "根据资料10，检查已完成。", "资料1.0版要求检查。"])
def test_reference_exemption_does_not_supply_missing_numbers(text):
    assert not validate(make_answerable_draft(text), make_context("要求核对并记录。", "背景说明。")).valid


@pytest.mark.parametrize("text", ["资料1件", "资料1.0", "资料10", "资料1#2", "资料1至3件", "资料1 4件"])
def test_label_boundary_does_not_swallow_other_facts(text):
    stripped = without_bound_citation_labels(text, ["资料1"])
    if text == "资料1 4件":
        assert "4件" in stripped
    else:
        assert stripped == text


def test_invalid_structured_citation_remains_invalid():
    result = validate(make_answerable_draft("根据资料999，已完成。", ["资料999"]), make_context("已完成。"))
    assert not result.valid and any("不存在的引用" in error for error in result.errors)


@pytest.mark.parametrize("location", ["question", "source"])
def test_reference_in_question_or_source_never_proves_a_quantity(location):
    context = make_context("请参照资料1核对。" if location == "source" else "要求核对。")
    question = "资料1要求检查几件？" if location == "question" else "检查几件？"
    assert not validate(make_answerable_draft("检查1件。"), context, question).valid
