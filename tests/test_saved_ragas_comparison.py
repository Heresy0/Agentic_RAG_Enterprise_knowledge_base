import copy

import pytest

from scripts.compare_saved_ragas import compare, render


def runs():
    sample=dict(id='one',user_input='问题',reference='短参考',response='固定答案',answerable=True,
        expected_answerable=True,original_status='passed',retrieved_contexts=['固定证据'])
    row=dict(id='one',original_status='passed',judge_screen_pass=False,
        factual_correctness=dict(status='ok',value=.4),faithfulness=dict(status='ok',value=1.0))
    before=(dict(records=[row],judge_usage=dict(calls=6)),dict(judge_model='judge'),[sample])
    after=copy.deepcopy(before)
    after[0]['records'][0]['factual_correctness']['value']=1.0
    after[0]['records'][0]['judge_screen_pass']=True
    after[2][0].update(reference='补全主体后的必答参考',original_reference='短参考',optional_reference_information=['可选信息'])
    return before,after


def test_comparison_changes_gold_only_and_counts_diagnostic_recovery():
    before,after=runs();summary=compare(before,after)
    assert summary['cases']==1
    assert summary['screen_recovered']==['one']
    assert summary['after_disagreements']==0
    assert summary['groups']['all']['factual_correctness']['delta']==.6
    assert summary['faithfulness_changes']['unchanged']==1
    assert '可选信息' in render(summary,before,after)


@pytest.mark.parametrize('fault',['model','context','answer','question','original_score','original_reference','missing','metric_error'])
def test_comparison_refuses_confounded_or_incomplete_runs(fault):
    before,after=runs()
    if fault=='model':after[1]['judge_model']='other'
    if fault=='context':after[2][0]['retrieved_contexts']=['换过的上下文']
    if fault=='answer':after[2][0]['response']='重生成答案'
    if fault=='question':after[2][0]['user_input']='新问题'
    if fault=='original_score':after[2][0]['original_status']='failed'
    if fault=='original_reference':after[2][0]['original_reference']='另一份原参考'
    if fault=='missing':after[0]['records'].clear()
    if fault=='metric_error':after[0]['records'][0]['faithfulness']['status']='error'
    with pytest.raises(ValueError):compare(before,after)
