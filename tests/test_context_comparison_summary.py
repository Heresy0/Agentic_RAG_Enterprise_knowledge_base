"""Offline checks for paired evaluation accounting and causal limitations."""
import json
import hashlib

import pytest

from scripts.summarize_context_comparison import percentile, summarize
from scripts.bind_context_reviews import bind
from backend.app.evaluation.semantics import review_binding


def write_json(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


def test_pairs_cost_dedup_errors_and_refusal_denominators(tmp_path):
    write_json(tmp_path/'protocol.json', {'kind':'paired','context_max_characters':12000})
    write_json(tmp_path/'frozen-criteria.json', {'cases':[
        {'id':'positive','answerable':True}, {'id':'negative','answerable':False},
        {'id':'broken','answerable':True}]})
    evidence={'knowledge_base_id':'kb','chunk_id':'child','parent_id':'parent','content':'condition; exception'}
    usage={'event_id':'same','stage':'context_selection','total_tokens':100,'input_tokens':80,'estimated_cost':'0.0002'}
    for name in ('full_parent','model_sections'):
        folder=tmp_path/name;folder.mkdir()
        rows=[dict(id='positive',latency_ms=100,response={'answerable':False,'answer':'refused'},stages={
            'authorized_final':[evidence], 'context':[evidence], 'parent_context':[evidence],
            'context_status':[{'incomplete_reason':None}], 'model_usage':[usage,usage]}),
            dict(id='negative',latency_ms=200,response={'answerable':False,'answer':'refused'},stages={'model_usage':[]}),
            dict(id='broken',latency_ms=300,error_type='Timeout',stages={'model_usage':[
                dict(event_id='unknown',stage='answer_generation',total_tokens=None,estimated_cost=None)]})]
        if name=='model_sections':
            rows[0]['stages']['context']=[{**evidence,'content':'condition'}]
            rows[0]['stages']['context_status']=[{'incomplete_reason':'budget'}]
        (folder/'traces.jsonl').write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf-8')
    result=summarize(tmp_path)
    full=result['variants']['full_parent'];model=result['variants']['model_sections']
    assert full['model_calls']==2 and full['total_tokens']==100
    assert full['estimated_cny']==pytest.approx(.0002)
    assert full['unpriced_calls']==1 and full['unknown_usage_calls']==1
    assert full['cost_complete'] is False
    assert full['responses']==2 and full['errors']==1
    assert full['refusal_rate']==1 and full['false_refusals']==1 and full['correct_refusals']==1
    assert full['final_parent_items']==dict(intact=1,changed=0,unverified=0)
    assert model['final_parent_items']['changed']==1 and model['incomplete_context_cases']==1
    assert result['pairs']['same_initial_evidence']==1
    assert result['pairs']['final_context_observed_pairs']==1
    assert result['pairs']['same_answerability']==2
    assert result['pairs']['changed_context_ids']==['positive']
    # No semantic-accuracy number can be inferred merely from successful requests.
    assert 'accuracy' not in full and 'semantic_accuracy' not in full


def test_unfinished_pressure_run_and_nearest_rank(tmp_path):
    write_json(tmp_path/'protocol.json', {'kind':'pressure'})
    write_json(tmp_path/'frozen-criteria.json', {'cases':[{'id':'a','answerable':True}]})
    result=summarize(tmp_path)
    assert result['protocol']['same_corpus'] is None
    assert result['protocol']['budget'] is None
    assert result['pairs']['completed']==0
    assert result['variants']['full_parent']['refusal_rate'] is None
    assert percentile([], .95) is None
    assert percentile([3,1,2], .95)==3


def test_review_binding_and_focus_refusals_are_not_counted_as_complete(tmp_path):
    case=dict(id='condition',question='what is allowed?',answerable=True)
    write_json(tmp_path/'frozen-criteria.json',dict(cases=[case],condition_focus_ids=['condition']))
    judgments=tmp_path/'judgments.json'
    write_json(judgments,dict(reviewer='assistant',rubric='conditions and exceptions',cases={
        'condition':dict(both='已有答案但错误拒答',fail=['full_parent','model_sections'])}))
    response=dict(answerable=False,answer='refusal')
    for name in ('full_parent','model_sections'):
        folder=tmp_path/name;folder.mkdir()
        write_json(folder/'traces.jsonl',dict(id='condition',question_hash=hashlib.sha256(case['question'].encode()).hexdigest(),
            response=response,stages=dict(context=[])))
    raw=json.loads(judgments.read_text(encoding='utf-8'))
    raw['observations_sha256']={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in {
        'criteria':tmp_path/'frozen-criteria.json',
        **{v:tmp_path/v/'traces.jsonl' for v in ('full_parent','model_sections')}}.items()}
    write_json(judgments,raw)
    result=bind(tmp_path,judgments)
    assert result['variants']['full_parent']['complete_semantic_accuracy']==0
    assert result['variants']['model_sections']['condition_focus']['categories']==dict(false_refusal=1)
    review=json.loads((tmp_path/'full_parent/assistant-reviews.jsonl').read_text(encoding='utf-8'))
    assert review['binding']==review_binding(case,response,[])
    with pytest.raises(ValueError,match='overwritten'):
        bind(tmp_path,judgments)
    # A changed answer invalidates the imported judgments, even when IDs match.
    (tmp_path/'full_parent/traces.jsonl').write_text('{}\n',encoding='utf-8')
    with pytest.raises(ValueError,match='frozen observations'):
        bind(tmp_path,judgments,tmp_path/'another-review')


def test_incomplete_reviews_do_not_create_any_outputs(tmp_path):
    write_json(tmp_path/'frozen-criteria.json',dict(cases=[dict(id='missing',answerable=True)]))
    judgments=tmp_path/'judgments.json'
    write_json(judgments,dict(reviewer='assistant',rubric='conditions',cases={}))
    for variant in ('full_parent','model_sections'):
        folder=tmp_path/variant;folder.mkdir()
        (folder/'traces.jsonl').write_text('',encoding='utf-8')
    raw=json.loads(judgments.read_text(encoding='utf-8'))
    raw['observations_sha256']={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in {
        'criteria':tmp_path/'frozen-criteria.json',
        **{v:tmp_path/v/'traces.jsonl' for v in ('full_parent','model_sections')}}.items()}
    write_json(judgments,raw)
    with pytest.raises(ValueError,match='exactly'):
        bind(tmp_path,judgments)
    assert not list(tmp_path.rglob('assistant-reviews.jsonl'))
