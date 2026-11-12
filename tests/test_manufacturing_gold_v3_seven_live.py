"""Check seven-case selection, payload isolation and the separate call budget."""
from copy import deepcopy
import pytest

from backend.app.quality.eval_budget import EvaluationStopped
from scripts.build_manufacturing_gold_100_v3 import CASE_IDS, BASE, revise, rows
from scripts.manufacturing_gold_v3_seven_live import BatchBudget, LIMITS, payload, validate_bindings


def test_revision_preserves_questions_sources_and_forbidden_conclusions():
    before=rows(BASE/'cases.jsonl');after=revise(before)
    assert [c['id'] for b,c in zip(before,after) if b != c] == list(CASE_IDS)
    for b,c in zip(before,after):
        assert all(b[k] == c[k] for k in ('question','input','evidence','provenance','test_mode'))
        assert b['expected']['forbidden_claims'] == c['expected']['forbidden_claims']
    assert '超过' in after[5]['expected']['required_facts'][0]['text']
    assert '重新' in after[6]['expected']['required_facts'][0]['text']


def test_payload_excludes_gold_labels_policy_and_historical_context():
    row=dict(selection=dict(work_order_id='WO',job_card_id='CARD'),question='当前卡也已开工吗？',knowledge_base_id=None,
        expected=dict(reference_answer='秘密金标'),review_policy='秘密规则',context=dict(status='Open'))
    assert payload(row) == dict(work_order_id='WO',job_card_id='CARD',question='当前卡也已开工吗？',knowledge_base_id=None)


def test_batch_budget_does_not_reuse_the_consumed_hundred_case_allowance():
    budget=BatchBudget(LIMITS)
    budget.calls=56
    with pytest.raises(EvaluationStopped):
        budget.before_call()
    assert budget.calls == 56 and budget.limited_by == 'calls'
    assert LIMITS == dict(max_model_calls=56,max_tokens=350000,max_seconds=900,reservation_tokens=32000)


def test_unknown_usage_reserves_tokens_and_stops_before_exceeding_limit():
    budget=BatchBudget(LIMITS)
    budget.charged_tokens=320000
    with pytest.raises(EvaluationStopped):
        budget.before_call()
    assert budget.calls == 0 and budget.limited_by == 'tokens'


@pytest.mark.parametrize('ids',[list(CASE_IDS[:-1]),list(CASE_IDS)+['MFG-GOLD-084'],list(reversed(CASE_IDS))])
def test_unapproved_case_selection_rejected(ids):
    frozen=dict(cases=[dict(case_id=i) for i in ids],limits=deepcopy(LIMITS),input_hashes={})
    with pytest.raises(ValueError,match='seven-case'):
        validate_bindings(frozen)
