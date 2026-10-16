import hashlib
import json

import pytest

from scripts.evaluate_saved_ragas import ROOT, apply_reference_overlay, digest, prepare_samples, response_text, score_value, screen


@pytest.fixture
def frozen(tmp_path):
    cases=[dict(id=str(i),question='问题'+str(i),reference_answer='标准答案',answerable=True) for i in range(2)]
    dataset=tmp_path/'dataset.jsonl'
    dataset.write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in cases),encoding='utf-8')
    traces=[dict(id=c['id'],question_hash=hashlib.sha256(c['question'].encode()).hexdigest(),
        response=dict(answer='最终答案',answerable=True,refusal_reason=None),
        stages=dict(context=[dict(citation_id='资料1',document_name='实际父块',content='实际选中的正文')],
                    answer_generation=[dict(answer='不可评分的草稿')])) for c in cases]
    report=dict(dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),layers=dict(answer=dict(records=[
        dict(id=t['id'],status='passed',response=t['response']) for t in traces])))
    rp=tmp_path/'report.json';tp=tmp_path/'traces.jsonl'
    def save():
        rp.write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
        tp.write_text(''.join(json.dumps(t,ensure_ascii=False)+'\n' for t in traces),encoding='utf-8')
    save()
    return dataset,rp,tp,traces,report,save


def test_reuses_only_final_answer_and_selected_context(frozen):
    dataset,rp,tp,_,_,_=frozen
    rows,_=prepare_samples(dataset,rp,tp,expected_cases=2)
    assert rows[0]['response']=='最终答案'
    assert rows[0]['retrieved_contexts']==['[资料1]\n文档：实际父块\n内容：实际选中的正文']


@pytest.mark.parametrize('fault',['question','answer','scope','missing','duplicate'])
def test_rejects_misaligned_or_unsafe_source_before_paid_calls(frozen,fault):
    dataset,rp,tp,traces,report,save=frozen
    if fault=='question':traces[0]['question_hash']='changed'
    if fault=='answer':report['layers']['answer']['records'][0]['response']={'answer':'其他答案'}
    if fault=='scope':traces[0]['stages']['context'][0]['scope_violation']=True
    if fault=='missing':traces[0]['stages'].pop('context')
    if fault=='duplicate':traces[1]['id']=traces[0]['id']
    save()
    with pytest.raises(ValueError):prepare_samples(dataset,rp,tp,expected_cases=2)


def test_refusal_keeps_visible_reason_without_promoting_a_draft():
    assert response_text(dict(answer='无法回答',answerable=False,refusal_reason='无实际日期'))=='无法回答\n拒答原因：无实际日期'


def test_undefined_or_error_metric_is_not_a_pass_or_zero():
    thresholds=dict(factual_correctness=.8,faithfulness=.9)
    assert score_value(float('nan')) is None
    assert screen(dict(factual_correctness=dict(status='ok',value=1)),thresholds) is None
    assert screen(dict(factual_correctness=dict(status='ok',value=.8),faithfulness=dict(status='ok',value=.9)),thresholds) is True
    assert screen(dict(factual_correctness=dict(status='ok',value=.79),faithfulness=dict(status='ok',value=1)),thresholds) is False


def test_real_reference_overlay_preserves_all_100_fact_criteria_and_optional_boundary():
    dataset=ROOT/'evals/archive/xinghai_v4/single_turn.jsonl'
    cases=[json.loads(l) for l in dataset.read_text(encoding='utf-8-sig').splitlines()]
    samples=[dict(id=c['id'],user_input=c['question'],reference=c['reference_answer'],
                  response='不应据此修改参考的候选答案',retrieved_contexts=['实际上下文']) for c in cases]
    rows,overlay=apply_reference_overlay(samples,dataset,dataset.parent/'scoring_rules.json',
                                        dataset.parent/'ragas_references_v1.json')
    assert len(rows)==100
    assert digest(dataset)=='24539fc4634bf406045b6d57528ef6d902ddcf539728a879124272108a0f115a'
    for old,new in zip(samples,rows):
        assert old['response']==new['response']
        assert old['retrieved_contexts']==new['retrieved_contexts']
        assert old['user_input']==new['user_input']
        assert old['reference']==new['original_reference']
    by_id={r['id']:r for r in rows};gold={r['id']:r for r in overlay['cases']}
    assert by_id['XH-014']['reference']=='星海公司的组织规模为180人。'
    assert '15分钟' not in by_id['XH-001']['reference']
    assert '15分钟' in ''.join(by_id['XH-001']['optional_reference_information'])
    assert '重建连接' not in by_id['CC-012']['reference']
    assert gold['CC-012']['required_facts']==['解析缓存','23分钟']
    assert gold['CC-012']['optional_facts']==['重建连接']
    assert '假设下' in by_id['NQ-008']['reference']
    assert '不能证明' in by_id['NQ-011']['reference']
    assert '23:20' in by_id['NQ-008']['reference']


@pytest.mark.parametrize('fault',['hash','duplicate','criteria','polarity','blank'])
def test_overlay_rejects_source_mismatch_or_fact_criteria_mutation(tmp_path,fault):
    dataset=ROOT/'evals/archive/xinghai_v4/single_turn.jsonl'
    cases=[json.loads(l) for l in dataset.read_text(encoding='utf-8-sig').splitlines()]
    samples=[dict(id=c['id'],reference=c['reference_answer']) for c in cases]
    overlay=json.loads((dataset.parent/'ragas_references_v1.json').read_text(encoding='utf-8'))
    if fault=='hash':overlay['dataset_sha256']='changed'
    if fault=='duplicate':overlay['cases'][-1]=overlay['cases'][0]
    if fault=='criteria':overlay['cases'][0]['required_facts']=[]
    if fault=='polarity':overlay['cases'][0]['answerable']=False
    if fault=='blank':overlay['cases'][0]['required_reference']=' '
    path=tmp_path/'overlay.json';path.write_text(json.dumps(overlay),encoding='utf-8')
    with pytest.raises(ValueError):
        apply_reference_overlay(samples,dataset,dataset.parent/'scoring_rules.json',path)
