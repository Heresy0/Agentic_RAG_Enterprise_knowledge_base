import json

from langchain_core.documents import Document

from backend.app.knowledge.answer_contract import answer_contract, asks_actual_completion_date
from backend.app.knowledge.answer_models import GenerationAnswerDraft
from backend.app.knowledge.answer_validation import AnswerValidator
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.knowledge.parent_context_selection import select_parent_context
from backend.app.knowledge.query_planner import QueryPlan, query_plan_scope


def parent():
    sections = [
        ('单位支援标准', '每个完整支援日按7小时登记。' + '单位标准说明。' * 20),
        ('适用范围', '适用于外派支援，常规值班不适用。'),
        ('办理步骤', '须取得负责人确认。\n级别|条件\nA|书面登记'),
        ('例外情况', '不得重复登记；尚未确认的时长不能当成实际时长。'),
        ('其他活动', '团建活动使用绿色标志。' * 50),
    ]
    content = '\n\n'.join('## ' + title + '\n\n' + body for title, body in sections)
    return Document(id='child', page_content=content, metadata=dict(
        chunk_type='parent', parent_id='parent', source='标准.md', document_id='doc',
        matched_child_section='单位支援标准', matched_child_content=sections[0][1],
        chunk_content_hash='original-parent-hash',
    ))


def test_default_selection_keeps_the_complete_parent_including_background():
    original = parent()
    selected = select_parent_context(original, '每个完整支援日的登记标准是多少？')
    assert selected.page_content == original.page_content
    assert original.metadata['matched_child_content'] in selected.page_content
    assert '常规值班不适用' in selected.page_content
    assert 'A|书面登记' in selected.page_content and '不得重复登记' in selected.page_content
    assert selected.metadata['parent_id'] == original.metadata['parent_id']
    for start, end in selected.metadata['parent_selection']['spans']:
        assert original.page_content[start:end].strip() in selected.page_content
    assert '绿色标志' in original.page_content  # Full preview source stays intact.


def test_multihop_and_comparison_keep_complete_parent_and_bridges():
    original = parent()
    plan = QueryPlan(intent='multi_part', strategy='agentic', reason='dependent_lookup')
    with query_plan_scope(plan):
        assert select_parent_context(original, '支援登记标准？').page_content == original.page_content
    assert select_parent_context(original, '两种登记标准是否相同？').page_content == original.page_content


def test_missing_or_unverifiable_anchor_never_guesses_a_slice():
    original = parent()
    original.metadata['matched_child_section'] = '不存在的章节'
    assert select_parent_context(original, '每个完整支援日的登记标准是多少？').page_content == original.page_content
    original.metadata['matched_child_section'] = '单位支援标准'
    original.metadata['matched_child_content'] = '父块没有的原文'
    assert select_parent_context(original, '每个完整支援日的登记标准是多少？').page_content == original.page_content


def test_selected_context_still_rejects_unsupported_numbers_and_missing_coverage():
    context = ContextBuilder().build([parent()], question='每个完整支援日的登记标准是多少？')
    draft = GenerationAnswerDraft.model_validate(dict(answerable=True, response_type='answer',
        claims=[dict(text='每个支援日登记999小时', citations=['资料1'])],
        coverage=[dict(requirement_id='Q1', aspect='标准', status='answered',
                       claim_indices=[1], missing_information=None)]))
    assert not AnswerValidator().validate(draft=draft, context=context, question='标准是多少？').valid
    draft.claims[0].text = '每个完整支援日按7小时登记'
    draft.coverage[0].requirement_id = 'Q9'
    result = AnswerValidator().validate(draft=draft, context=context, question='标准是多少？')
    assert not result.valid and any('缺少必答' in error for error in result.errors)


def test_contract_is_guidance_not_added_obligations_or_claims():
    data = json.loads(answer_contract('每个完整支援日的标准分别是多少，两个标准是否相同？', [dict(id='Q1')]))
    assert data['requirement_ids'] == ['Q1']
    assert any('实际累计量' in rule for rule in data['guidance'])
    assert any('相同或不同' in rule for rule in data['guidance'])
    assert not {'claims', 'tenant_id', 'knowledge_base_id', 'budget'}.intersection(data)


def test_plan_status_guidance_does_not_allow_unknown_status_as_definite_noncompletion():
    rules = json.loads(answer_contract('问题计划何时修复，是否已修复？', [dict(id='Q1')]))['guidance']
    assert any('不能改称' in rule for rule in rules)
    assert any('没有状态或边界依据时仍按缺证据拒答' in rule for rule in rules)
    assert any('answerable=true' in rule for rule in rules)
    assert not any('不是所需日期' in rule for rule in rules)


def test_unknown_actual_date_requires_refusal_instead_of_a_boundary_disguised_as_date():
    rules = json.loads(answer_contract('整改实际在哪天上线完成？', [dict(id='Q1')]))['guidance']
    assert any('answerable=false' in rule and '不是所需日期' in rule for rule in rules)


def test_planned_date_does_not_create_an_actual_date_requirement_in_a_separate_status_clause():
    for question in ['乙任务计划什么时候修复，是否已经修复？',
                     '甲任务何时计划修复；是否已修复？',
                     '丙项目原定哪天最终验收？是否已经验收？']:
        assert not asks_actual_completion_date(question)
    for question in ['乙任务实际在哪天修复？', '甲任务计划哪天上线，实际在哪天签署？',
                     '丙项目最终验收日期是什么？', '项目原计划与实际完成日期分别是什么？']:
        assert asks_actual_completion_date(question)


def test_date_guidance_preserves_the_source_form_instead_of_inventing_a_year():
    rules = json.loads(answer_contract('丙项目计划哪天验收，实际在哪天签署？', [dict(id='Q1')]))['guidance']
    assert any('原文只写月日就只写月日' in rule for rule in rules)
    assert any('不得从文件名' in rule for rule in rules)


def test_decisions_require_a_rule_measurement_chain_and_identity_mechanism():
    rules = json.loads(answer_contract('当前批次是否满足验收条件？不同空间相同object_id能否合并去重？', [dict(id='Q1'),dict(id='Q2')]))['guidance']
    assert any('总体判定' in rule and '引用规则正文' in rule for rule in rules)
    assert any('所有字段原名' in rule for rule in rules)
    assert not any('tenant_id' in rule or 'event_id' in rule for rule in rules)


def test_response_layout_distinguishes_status_boundary_from_unknown_actual_date():
    status = json.loads(answer_contract('乙任务计划何时修复，是否已修复？', [dict(id='Q1')]))
    assert status['response_layout']['example']['answerable']
    assert all(row['status']=='answered' for row in status['response_layout']['example']['coverage'])
    date = json.loads(answer_contract('乙任务计划何时修复，实际哪天修复？', [dict(id='Q1')]))
    assert 'response_layout' not in date
    judgment = json.loads(answer_contract('当前批次是否满足全部条件？', [dict(id='Q1')]))
    assert '整体' in judgment['response_layout']['first_claim']
