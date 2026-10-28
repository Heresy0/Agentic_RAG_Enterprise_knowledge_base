"""Memory correctness/security with synthetic models; no external calls."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import update

from test_conversations import data, client, Answer, Resolver, result
from backend.app.db.conversation_models import Conversation
from backend.app.db.models import KnowledgeBase, User
from backend.app.evaluation.trace import capture_trace
from backend.app.knowledge.conversation_memory import (
    MAX_BYTES, MemoryResolution, Proposal, clarification_reason, empty_state, encoded, item_key, merge,
)
from backend.app.knowledge.conversation_service import ConversationAnswerService, ConversationResolver
from backend.app.knowledge.conversations import ConversationBusy, ConversationStore
from backend.app.security.authorization import AuthorizationDenied
from backend.app.security.retrieval_scope import RetrievalScope


class MemoryResolver(Resolver):
    def __init__(self, draft=None, enabled=True):
        super().__init__()
        self._settings = SimpleNamespace(conversation_memory_enabled=enabled)
        self.draft = draft
        self.memory_calls = []

    def resolve_memory(self, question, turns, state, current_turn):
        self.memory_calls.append((question, turns, state.model_copy(deep=True), current_turn))
        return self.draft(question, turns, state, current_turn) if callable(self.draft) else (
            self.draft or MemoryResolution(question=question))


def invoke(data, service, cid, question='青屿的条件是什么？', callback=None):
    return service.answer(question, conversation_id=cid, principal=data.principal,
                          scope=data.scope, session=data.session, commit_success=callback)


def seed(data, count=6, first='请查询青屿项目2024年标准，排除外包，但包括已转正员工。'):
    cid = data.store.create().id
    service = ConversationAnswerService(Answer(), Resolver())
    for i in range(count):
        invoke(data, service, cid, first if i == 0 else '它的要求是什么？')
    return cid


def proposal(field='time', value='2024年', turn=1, quote='请查询青屿项目2024年标准', **kwargs):
    return Proposal(object='青屿', field=field, value=value, turn=turn, quote=quote, **kwargs)


def merged(data, questions, updates, *, state=None, current=None):
    return merge(state or empty_state(data.principal, data.scope), MemoryResolution(question='查询', updates=updates),
                 questions, current or max(questions), map(str, data.scope.knowledge_base_ids))


def test_fifteen_turns_retain_early_object_time_negation_exception_without_extra_call(data):
    first = '请查询青屿项目2024年标准，排除外包，但包括已转正员工。'
    def draft(q, turns, state, index):
        updates = [] if state.items else [proposal(field=f, value=v, quote=first) for f, v in (
            ('subject', '青屿'), ('time', '2024年'), ('exclusion', '排除外包'), ('exception', '包括已转正员工'))]
        return MemoryResolution(question='青屿项目2024年排除外包但包括已转正员工的要求是什么？', updates=updates)
    answer, resolver = Answer(), MemoryResolver(draft)
    service = ConversationAnswerService(answer, resolver)
    cid = data.store.create().id
    for index in range(15):
        invoke(data, service, cid, first if index == 0 else '它的要求是什么？')
    assert len(resolver.histories) == 6 and len(resolver.memory_calls) == 9
    last = resolver.memory_calls[-1]
    assert [t.turn_index for t in last[1]] == list(range(9, 15))
    assert all(t.question != first for t in last[1])
    assert {i.value for i in last[2].items} == {'青屿', '2024年', '排除外包', '包括已转正员工'}
    assert answer.questions[-1] == '青屿项目2024年排除外包但包括已转正员工的要求是什么？'
    state = data.store.context_memory(cid)
    assert state.coverage_start == 1 and state.covered_through == 15
    assert all(i.source.turn == 1 and i.source.quote == first for i in state.items)
    assert len(encoded(state)) <= MAX_BYTES


def test_old_long_conversation_bootstraps_only_readable_window(data):
    cid = seed(data, count=14)
    resolver = MemoryResolver()
    invoke(data, ConversationAnswerService(Answer(), resolver), cid)
    assert resolver.memory_calls[0][2].coverage_start == 9
    assert [t.turn_index for t in resolver.memory_calls[0][1]] == list(range(9, 15))


def test_explicit_correction_keeps_source_and_supersedes_old_condition(data):
    q = {1: '查询青屿2024年标准', 2: '青屿改查2025年标准'}
    state = merged(data, {1: q[1]}, [proposal(quote=q[1])])
    state = merged(data, q, [proposal(value='2025年', turn=2, quote=q[2], operation='replace')], state=state)
    assert [(i.value, i.status, i.source.turn) for i in state.items] == [
        ('2024年', 'superseded', 1), ('2025年', 'active', 2)]


def test_current_query_year_overrides_old_year_without_replacing_business_claim(data):
    q = {1: '青屿合同金额为3000元，查询2024年标准', 2: '青屿2025年呢？'}
    state = merged(data, {1: q[1]}, [proposal(quote=q[1]), proposal(field='claim', value='合同金额为3000元', quote=q[1])])
    state = merged(data, q, [proposal(value='2025年', turn=2, quote=q[2])], state=state)
    assert next(i for i in state.items if i.field == 'claim').status == 'active'
    assert next(i for i in state.items if i.value == '2024年').status == 'superseded'


def test_bootstrap_replays_unprocessed_corrections_in_user_turn_order(data):
    questions = {1: '青屿查询2024年标准', 3: '青屿2025年呢？', 5: '更正青屿范围为包括外包', 7: '它的要求？'}
    updates = [proposal(quote=questions[1]), proposal(value='2025年', quote=questions[3], turn=3),
               proposal(field='filter', value='包括外包', quote=questions[5], turn=5, operation='replace')]
    state = merged(data, questions, updates)
    assert next(i for i in state.items if i.value == '2024年').status == 'superseded'
    assert next(i for i in state.items if i.value == '2025年').status == 'active'
    with pytest.raises(ValueError):
        merged(data, questions, [updates[-1]], state=state, current=8)


@pytest.mark.parametrize('original,quote,value', [
    ('假设青屿金额为3000元', '青屿金额为3000元', '3000元'),
    ('青屿金额是不是3000元？', '3000元', '3000元'),
])
def test_cropped_quote_cannot_promote_hypothesis_or_question_to_claim(data, original, quote, value):
    with pytest.raises(ValueError):
        merged(data, {1: original}, [proposal(field='claim', value=value, quote=quote)])


def test_cropped_quote_cannot_hide_a_negated_correction(data):
    with pytest.raises(ValueError):
        merged(data, {1: '不要将青屿改为外包'}, [proposal(field='filter', value='外包',
            quote='青屿改为外包', operation='replace')])


def test_ambiguous_business_claim_conflicts_only_for_relevant_object(data):
    q = {1: '青屿合同金额为3000元', 2: '青屿合同金额为4000元'}
    state = merged(data, q, [proposal(field='claim', value=v, quote=q[t], turn=t) for t, v in (
        (1, '合同金额为3000元'), (2, '合同金额为4000元'))])
    assert {i.status for i in state.items} == {'conflicted'}
    assert '冲突' in clarification_reason(state, MemoryResolution(question='青屿要求？'), '青屿要求？', '青屿要求？')
    assert clarification_reason(state, MemoryResolution(question='星海令牌要求？'), '星海令牌要求？', '星海令牌要求？') is None


def test_selecting_existing_conflicted_value_resolves_it_with_explicit_correction(data):
    q = {1: '青屿范围包括外包', 2: '青屿范围排除外包'}
    state = merged(data, q, [proposal(field='filter', value=v, quote=q[t], turn=t) for t, v in (
        (1, '包括外包'), (2, '排除外包'))])
    q[3] = '更正：青屿范围包括外包'
    state = merged(data, q, [proposal(field='filter', value='包括外包', turn=3, quote=q[3], operation='replace')], state=state)
    assert [(i.value, i.status) for i in state.items if i.status != 'superseded'] == [('包括外包', 'active')]


def test_different_business_periods_are_separate_keys(data):
    q = {1: '青屿2024年合同金额为3000元', 2: '青屿2025年合同金额为4000元'}
    state = merged(data, q, [proposal(field='claim', value=v, quote=q[t], turn=t, qualifier=year) for t, v, year in (
        (1, '合同金额为3000元', '2024年'), (2, '合同金额为4000元', '2025年'))])
    assert len({i.key for i in state.items}) == 2 and {i.status for i in state.items} == {'active'}


@pytest.mark.parametrize('bad', [
    dict(quote='助手说青屿2024年标准'), dict(value='2025年'), dict(turn=99),
    dict(object='不存在的项目'), dict(qualifier='不存在的时期'), dict(operation='replace', field='filter'),
    dict(field='claim', quote='青屿金额是不是3000元？', value='3000元'),
    dict(field='claim', quote='假设青屿金额是3000元', value='3000元'),
])
def test_unverified_sources_and_guessed_corrections_rejected_as_whole_delta(data, bad):
    base = dict(object='青屿', field='time', value='2024年', turn=1, quote='青屿2024年标准')
    base.update(bad)
    questions = {1: '青屿2024年标准；青屿金额是不是3000元？假设青屿金额是3000元'}
    with pytest.raises(ValueError):
        merged(data, questions, [Proposal(**base)])


@pytest.mark.parametrize('value,rewritten', [
    ('3000元', '青屿3000元减去1000元是多少？'),
    ('三千元', '青屿三千元减去一千元是多少？'),
])
def test_historical_numbers_do_not_become_current_calculation_premises(data, value, rewritten):
    q = {1: f'青屿合同金额为{value}'}
    state = merged(data, q, [proposal(field='claim', value=value, quote=q[1])])
    assert clarification_reason(state, MemoryResolution(question=rewritten), '它减去1000元是多少？', rewritten)
    assert clarification_reason(state, MemoryResolution(question=f'青屿{value}是多少？'), f'青屿{value}是多少？', f'青屿{value}是多少？') is None


def test_temporal_filter_cannot_whitelist_same_number_as_money(data):
    q = {1: '青屿2024年标准'}
    state = merged(data, q, [proposal(quote=q[1])])
    assert clarification_reason(state, MemoryResolution(question='青屿2024年标准？'), '它的标准？', '青屿2024年标准？') is None
    assert clarification_reason(state, MemoryResolution(question='青屿金额2024元？'), '它金额多少？', '青屿金额2024元？')


@pytest.mark.parametrize('current,rewritten', [
    ('2小时减去1小时是多少？', '青屿2元减去1元是多少？'),
    ('3000减去1000是多少？', '青屿3000元减去1000元是多少？'),
])
def test_current_digits_without_matching_units_cannot_authorize_historical_money(data, current, rewritten):
    quote = '青屿合同金额为3000元，另一个金额为2元'
    state = merged(data, {1: quote}, [proposal(field='claim', value='合同金额为3000元', quote=quote)])
    assert clarification_reason(state, MemoryResolution(question=rewritten), current, rewritten)


@pytest.mark.parametrize('value,query', [('V2版', '青屿V2版要求？'), ('2.0', '青屿版本2.0要求？')])
def test_query_version_can_carry_without_becoming_a_business_number(data, value, query):
    q = {1: f'青屿采用{value}'}
    state = merged(data, q, [proposal(field='version', value=value, quote=q[1])])
    assert clarification_reason(state, MemoryResolution(question=query), '它的要求？', query) is None
    assert clarification_reason(state, MemoryResolution(question='青屿金额2.0元？'), '它金额多少？', '青屿金额2.0元？')


@pytest.mark.parametrize('quote', ['青屿不要更正为外包', '假设青屿改为外包'])
def test_negated_or_hypothetical_correction_cannot_replace_state(data, quote):
    with pytest.raises(ValueError):
        merged(data, {1: quote}, [proposal(field='filter', value='外包', quote=quote, operation='replace')])


def test_model_schema_error_becomes_safe_clarification_without_a_second_call(data):
    cid = seed(data)
    resolver = MemoryResolver(lambda *_: {'question': '青屿要求？', 'updates': [{'forged': 'source'}]})
    answer = Answer()
    returned, _ = invoke(data, ConversationAnswerService(answer, resolver), cid)
    assert not returned.answerable and not returned.citations and not answer.questions
    assert len(resolver.memory_calls) == 1 and data.store.context_memory(cid).incomplete


def test_denied_single_scope_read_records_reset_before_later_regrant(data, client):
    cid = seed(data)
    path = f'/api/knowledge-bases/{data.scope.knowledge_base_id}/conversations/{cid}'
    data.session.execute(update(KnowledgeBase).where(KnowledgeBase.id == data.scope.knowledge_base_id).values(status='disabled'))
    data.session.commit()
    assert client.get(path).status_code == 403
    assert data.store.get(cid).memory_json['cutoff'] == 6
    data.session.execute(update(KnowledgeBase).where(KnowledgeBase.id == data.scope.knowledge_base_id).values(status='active'))
    data.session.commit()
    response = client.get(path)
    assert response.status_code == 200 and '排除外包' not in response.text
    assert data.store.turns(cid, for_context=True) == []


def test_dependency_union_includes_new_scope_even_with_no_new_state_entries(data):
    first_scope = RetrievalScope(data.principal.tenant_id, knowledge_base_ids=frozenset({data.scope.knowledge_base_id}))
    both_scope = RetrievalScope(data.principal.tenant_id, knowledge_base_ids=first_scope.knowledge_base_ids | {data.other_kb.id})
    store = ConversationStore(data.session, data.principal, first_scope)
    cid = store.create().id
    service = ConversationAnswerService(Answer(), MemoryResolver())
    for scope in [first_scope] * 7 + [both_scope]:
        service.answer('青屿要求？', conversation_id=cid, principal=data.principal, scope=scope, session=data.session)
    both_store = ConversationStore(data.session, data.principal, both_scope)
    assert set(both_store.context_memory(cid).dependency_ids) == set(map(str, both_scope.knowledge_base_ids))
    # Revoking that newly used base resets everything, even though no item mentions it.
    assert store.context_memory(cid).cutoff == 8


def test_refusal_can_commit_verified_user_query_conditions(data):
    cid = seed(data)
    class RefusingAnswer(Answer):
        def answer(self, *args, **kwargs):
            return result().model_copy(update={'answer': '资料不足', 'answerable': False, 'citations': []})
    draft = MemoryResolution(question='青屿2024年要求？', updates=[proposal(quote=data.store.turns(cid)[0].question)])
    returned, _ = invoke(data, ConversationAnswerService(RefusingAnswer(), MemoryResolver(draft)), cid)
    assert not returned.answerable and data.store.context_memory(cid).items[0].value == '2024年'


def test_active_capacity_overflow_never_silently_evicts_a_constraint(data):
    q = {1: '青屿' + '；'.join(f'限制{i}' for i in range(17))}
    state = merged(data, q, [proposal(field='filter', value=f'限制{i}', qualifier=f'限制{i}', quote=q[1]) for i in range(16)])
    with pytest.raises(ValueError, match='capacity'):
        merged(data, q, [proposal(field='filter', value='限制16', qualifier='限制16', quote=q[1])], state=state)
    assert len(state.items) == 16


def test_relevant_conflict_clarifies_before_rag_and_is_saved_without_citations(data):
    cid = seed(data, first='青屿范围包括外包')
    draft = MemoryResolution(question='青屿要求？', updates=[
        proposal(field='filter', value='包括外包', quote='青屿范围包括外包'),
        proposal(field='filter', value='排除外包', quote='青屿范围排除外包', turn=7),
    ])
    answer = Answer()
    returned, _ = invoke(data, ConversationAnswerService(answer, MemoryResolver(draft)), cid, '青屿范围排除外包')
    assert not returned.answerable and returned.citations == [] and '冲突' in returned.answer
    assert answer.questions == []
    assert data.store.get(cid).turn_count == 7
    assert {i.status for i in data.store.context_memory(cid).items} == {'conflicted'}


def test_invalid_update_preserves_previous_state_and_asks_clarification(data):
    cid = seed(data)
    answer = Answer()
    draft = MemoryResolution(question='青屿要求？', updates=[proposal(quote='伪造的青屿2024年标准')])
    returned, _ = invoke(data, ConversationAnswerService(answer, MemoryResolver(draft)), cid)
    assert not returned.answerable and returned.citations == [] and answer.questions == []
    assert data.store.context_memory(cid).incomplete


@pytest.mark.parametrize('stage', ['model', 'answer', 'audit', 'expired'])
def test_failed_requests_never_commit_turn_or_proposed_memory(data, stage):
    cid = seed(data)
    def draft(q, turns, state, index):
        if stage == 'model':
            raise TimeoutError('model timeout')
        if stage == 'expired':
            data.session.execute(update(Conversation).where(Conversation.id == cid).values(
                lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            data.session.commit()
        return MemoryResolution(question='青屿2024年标准？', updates=[proposal(quote=turns[0].question)])
    answer = Answer()
    if stage == 'answer':
        answer.error = RuntimeError('answer failed')
    def audit(_):
        raise RuntimeError('audit failed')
    with pytest.raises((RuntimeError, TimeoutError, ConversationBusy)):
        invoke(data, ConversationAnswerService(answer, MemoryResolver(draft)), cid,
               callback=audit if stage == 'audit' else None)
    row = data.store.get(cid)
    assert row.memory_json is None and row.turn_count == 6 and row.lease_token is None


def test_disabled_summary_preserves_legacy_resolver_and_six_turn_window(data):
    cid = seed(data, count=9)
    resolver = MemoryResolver(enabled=False)
    invoke(data, ConversationAnswerService(Answer(), resolver), cid)
    assert not resolver.memory_calls and len(resolver.histories[0]) == 6
    assert data.store.get(cid).memory_json is None


def test_read_revocation_and_regrant_cannot_resurrect_old_summary_or_prefix(data):
    both = RetrievalScope(data.principal.tenant_id, knowledge_base_ids=frozenset(
        {data.scope.knowledge_base_id, data.other_kb.id}))
    store = ConversationStore(data.session, data.principal, both)
    cid = store.create().id
    service = ConversationAnswerService(Answer(), Resolver())
    for _ in range(7):
        service.answer('青屿内部条件', conversation_id=cid, principal=data.principal, scope=both, session=data.session)
    state = merged(data, {1: '青屿内部条件'}, [proposal(field='subject', value='青屿', quote='青屿内部条件')])
    state.knowledge_base_id = None; state.mode = 'all_accessible'
    state.dependency_ids = sorted(map(str, both.knowledge_base_ids)); state.covered_through = 7
    data.session.execute(update(Conversation).where(Conversation.id == cid).values(memory_json=state.model_dump(mode='json')))
    data.session.commit()
    narrow = ConversationStore(data.session, data.principal, RetrievalScope(data.principal.tenant_id,
        knowledge_base_ids=frozenset({data.scope.knowledge_base_id})))
    assert all('内部条件' not in t.question for t in narrow.turns(cid))
    assert narrow.get(cid).memory_json['cutoff'] == 7
    # Permission is granted again, but the recorded reset continues to exclude the prefix.
    assert store.turns(cid, for_context=True) == []
    assert store.context_memory(cid).items == []


@pytest.mark.parametrize('stage', ['rewrite', 'rag'])
def test_revocation_during_request_blocks_result_and_persists_privacy_checkpoint(data, stage):
    cid = seed(data)
    def revoke():
        data.session.execute(update(KnowledgeBase).where(KnowledgeBase.id == data.scope.knowledge_base_id).values(status='disabled'))
        data.session.commit()
    def draft(q, turns, state, index):
        if stage == 'rewrite':
            revoke()
        return MemoryResolution(question='青屿要求？')
    class RevokingAnswer(Answer):
        def answer(self, *args, **kwargs):
            revoke()
            return result()
    service = ConversationAnswerService(RevokingAnswer() if stage == 'rag' else Answer(), MemoryResolver(draft))
    with capture_trace() as trace:
        with pytest.raises(AuthorizationDenied):
            invoke(data, service, cid)
    if stage == 'rewrite':
        assert 'conversation_resolution' not in trace.stages
    row = data.store.get(cid)
    assert row.turn_count == 6 and row.memory_json['cutoff'] == 6 and row.memory_json['items'] == []
    assert row.lease_token is None


def test_deactivated_user_is_checked_again_after_rewrite(data):
    cid = seed(data)
    def draft(q, turns, state, index):
        data.session.execute(update(User).where(User.id == data.principal.user_id).values(status='disabled'))
        data.session.commit()
        return MemoryResolution(question='青屿要求？')
    with pytest.raises(AuthorizationDenied):
        invoke(data, ConversationAnswerService(Answer(), MemoryResolver(draft)), cid)
    assert data.store.get(cid).turn_count == 6


def test_read_checkpoint_prevents_old_inflight_lease_from_reintroducing_summary(data):
    cid = seed(data)
    token = data.store.acquire(cid)
    state = data.store.context_memory(cid, token=token)
    data.store._checkpoint(data.store.get(cid), token=token)
    with pytest.raises(ConversationBusy):
        data.store.append(cid, token, 'q', 'q', result(), memory=state, expected_cutoff=state.cutoff)
    data.store.release(cid, token)
    assert data.store.get(cid).turn_count == 6 and data.store.get(cid).memory_json['cutoff'] == 6


@pytest.mark.parametrize('change', ['owner', 'mode', 'quote'])
def test_tampered_persisted_state_fails_closed(data, change):
    cid = seed(data)
    first = data.store.turns(cid)[0].question
    state = merged(data, {1: first}, [proposal(quote=first)])
    state.covered_through = 6
    raw = state.model_dump(mode='json')
    if change == 'owner':
        raw['user_id'] = str(data.other_user.id)
    elif change == 'mode':
        raw['mode'] = 'all_accessible'
    else:
        raw['items'][0]['source']['quote'] = '伪造的青屿2024年标准'
    data.session.execute(update(Conversation).where(Conversation.id == cid).values(memory_json=raw))
    data.session.commit()
    state = data.store.context_memory(cid)
    assert not state.items and state.cutoff == 6


def test_single_rewrite_call_has_bounded_raw_user_sources_and_no_assistant_evidence(data, monkeypatch):
    calls = []
    class Model:
        def with_structured_output(self, schema, **kwargs):
            return self
        def invoke(self, messages):
            calls.append(messages)
            return MemoryResolution(question='青屿要求？')
    monkeypatch.setattr('backend.app.knowledge.conversation_service.ChatOpenAI', lambda **_: Model())
    settings = SimpleNamespace(chat_model='fake', chat_base_url='http://unused', chat_api_key='fake', conversation_memory_enabled=True)
    resolver = ConversationResolver(settings)
    cid = seed(data)
    with capture_trace() as trace:
        invoke(data, ConversationAnswerService(Answer(), resolver), cid)
    assert len(calls) == 1
    payload = json.loads(calls[0][1].content)
    assert len(payload['history']) == 6 and payload['current_turn'] == 7
    assert all(set(t) == {'turn', 'question'} for t in payload['history'])
    assert '有效期为 2 小时' not in calls[0][1].content
    event = trace.stages['conversation_memory'][0]
    assert set(event) == {'outcome', 'item_count', 'conflict_count'}
    assert len(trace.stages['model_usage']) == 1
