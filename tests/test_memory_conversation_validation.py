"""Checks for the live evaluation harness, without external models or API calls."""
from types import SimpleNamespace

from backend.app.knowledge.conversation_memory import Item, MemoryState, Source, item_key
from scripts.run_memory_conversation_validation import check_turn, usage


def state(status='active'):
    return MemoryState(tenant_id='test-tenant', user_id='test-user', mode='all_accessible', knowledge_base_id=None,
        dependency_ids=['test-kb'], coverage_start=1, covered_through=7,
        items=[Item(key=item_key('住宿标准', '', 'filter'), object='住宿标准', qualifier='', field='filter',
            value='上海', source=Source(turn=1, quote='查询北京与上海住宿标准'), status=status)])


def test_source_quote_alone_does_not_prove_active_condition_retention():
    checks = check_turn({'expect': {'active': [{'contains': ['北京']}]}}, {}, '', state(), [], [])
    assert not checks['active_北京']


def test_superseded_items_do_not_count_as_active_conditions():
    checks = check_turn({'expect': {'active': [{'contains': ['上海']}]}}, {}, '', state('superseded'), [], [])
    assert not checks['active_上海']


def test_generic_clarification_is_not_proof_of_a_persisted_conflict():
    checks = check_turn({'expect': {'conflict': True, 'clarification': True}},
        {'answerable': False, 'citations': [], 'refusal_reason': '会话条件更新无法确认'}, '', state(), [], [])
    assert checks['clarification_expected'] and not checks['conflict_expected']


def test_retention_checkpoint_rejects_condition_repeated_in_raw_window():
    checks = check_turn({'expect': {'window_excludes': ['上海']}}, {}, '', state(),
                        [SimpleNamespace(question='上海呢？')], [])
    assert not checks['raw_window_excludes_上海']


def test_clarification_checks_actual_model_stages_and_rewrite_call_bound():
    events = [SimpleNamespace(stage='conversation_rewrite'), SimpleNamespace(stage='conversation_rewrite'),
              SimpleNamespace(stage='answer_generation')]
    checks = check_turn({'expect': {'no_rag_calls': True}}, {}, '', state(), [], events)
    assert not checks['no_rag_calls'] and not checks['rewrite_call_bound']


def test_unknown_tokens_are_reported_separately_from_known_total():
    result = usage([SimpleNamespace(stage='conversation_rewrite', total_tokens=None, status='error'),
                    SimpleNamespace(stage='answer_generation', total_tokens=30, status='success')])
    assert result['call_count'] == 2 and result['known_tokens'] == 30 and result['unknown_usage_calls'] == 1
