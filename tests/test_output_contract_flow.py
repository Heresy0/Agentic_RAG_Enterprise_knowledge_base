"""Generation/repair share constraints and only recover expected failures."""
import pytest
from sqlalchemy.exc import SQLAlchemyError

from test_answer_repair import repair_service, draft, execute as repair
from test_query_planner import planner, execute as plan
from test_evidence_coordinator import setup as agent_setup, collect
from test_evidence_feedback import setup as feedback_setup, execute as feedback
from test_budgeted_parent_context import selector, parent, request, QUESTION
from test_answer_service import make_document
from backend.app.knowledge.answer_contract import direct_answer_errors
from backend.app.knowledge.answer_validation import AnswerValidator
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.knowledge.output_contract import prepare_draft, validate_output, generation_schema
from backend.app.knowledge.rag_budget import RagBudget, rag_budget_scope
from backend.app.quality.eval_budget import EvaluationStopped
from backend.app.security.authorization import AuthorizationDenied
from backend.app.security.business_scope import BusinessAuthorizationDenied
from backend.app.integrations.mes import MESError, MESContextChanged
from backend.app.manufacturing.scope import ManufacturingScopeChanged


@pytest.mark.parametrize("error", [
    RuntimeError, ValueError, KeyError, SQLAlchemyError, AuthorizationDenied,
    BusinessAuthorizationDenied, ManufacturingScopeChanged, EvaluationStopped, MESError, MESContextChanged,
])
@pytest.mark.parametrize("stage", ["repair", "schema", "planning", "agent", "parent", "feedback"])
def test_nonrecoverable_failure_propagates_without_fallback(monkeypatch, error, stage):
    exc = error() if error in (BusinessAuthorizationDenied, MESError, MESContextChanged) else error("synthetic failure")
    with pytest.raises(error):
        if stage == "repair":
            service, calls, _ = repair_service(exc)
            repair(service, draft())
            pytest.fail("repair returned fallback")
        elif stage == "schema":
            service, calls, _ = repair_service(exc)
            with rag_budget_scope(RagBudget()):
                service.recover_schema(messages=[], scope=None, session=None, refresh_context=lambda: True)
            pytest.fail("schema recovery returned fallback")
        elif stage == "planning":
            service, _, calls = planner(exc)
            plan(service, "比较甲工艺和乙工艺")
        elif stage == "agent":
            scope, _, _, decider, coordinator = agent_setup(actions=[exc])
            collect(scope, coordinator)
        elif stage == "parent":
            selected, calls = selector(exc)
            document = parent()
            with request():
                selected.select(document, question=QUESTION, max_characters=180)
        else:
            service, _, _, calls, _, _ = feedback_setup(monkeypatch, candidate=exc)
            feedback(service)


def test_first_and_final_validation_keep_same_direct_answer_constraint():
    question = "是否满足验收要求？"
    original = draft(text="温度是80摄氏度", indices=(1,))
    context = ContextBuilder().build([make_document("温度是80摄氏度。")])
    validator = AnswerValidator()
    first = validate_output(original, validator=validator, context=context, question=question)
    final = validate_output(original, validator=validator, context=context, question=question,
                            coverage=first.coverage_errors)
    assert not first.valid and not final.valid
    assert set(direct_answer_errors(question, original)) <= set(first.errors) & set(final.errors)
    assert len(final.errors) == len(set(final.errors))


def test_final_supplement_does_not_erase_original_missing_coverage():
    context = ContextBuilder().build([make_document("温度是80摄氏度。")])
    missing = draft(indices=(2,))
    first = validate_output(missing, validator=AnswerValidator(), context=context, question="温度多少？")
    fixed = draft(indices=(1,))
    final = validate_output(fixed, validator=AnswerValidator(), context=context, question="温度多少？",
                            coverage=first.coverage_errors)
    assert first.coverage_errors and not final.valid


def test_ordinary_generation_contract_and_diagnostic_cleanup_are_unchanged():
    from backend.app.knowledge.answer_models import EvidenceFirstAnswerDraft
    schema = generation_schema(ContextBuilder().build([]))
    assert schema is EvidenceFirstAnswerDraft
    assert "business_field" not in schema.model_json_schema()["$defs"]["AnswerClaim"]["properties"]
    value = EvidenceFirstAnswerDraft.model_validate(dict(answerable=False, response_type="clarify", claims=[],
        missing_fields=["型号"], clarification_questions=["型号是什么？"],
        evidence_gap=dict(reason="missing_evidence", requirement_ids=["Q1"], source_quote="型号"),
        coverage=[dict(requirement_id="Q1", aspect="型号", status="insufficient", claim_indices=[],
            missing_information="型号缺失", source_quote="无关的诊断原文")]))
    prepared = prepare_draft(value)
    assert prepared.evidence_gap is None and prepared.coverage[0].source_quote is None
    assert value.evidence_gap is not None and value.coverage[0].source_quote is not None


def test_shared_validation_does_not_promote_an_explicit_validator_rejection():
    from types import SimpleNamespace
    from backend.app.knowledge.answer_validation import ValidationResult
    validator = SimpleNamespace(validate=lambda **_: ValidationResult(valid=False))
    context = ContextBuilder().build([])
    assert not validate_output(draft(indices=(1,)), validator=validator, context=context,
                               question="温度多少？").valid


def test_manufacturing_request_cannot_downgrade_schema_with_an_empty_context():
    from types import SimpleNamespace
    from backend.app.manufacturing.answer_context import manufacturing_answer_context
    from backend.app.manufacturing.generation_models import ManufacturingGenerationDraft
    context = ContextBuilder().build([])
    with manufacturing_answer_context(SimpleNamespace()):
        assert generation_schema(context) is ManufacturingGenerationDraft
