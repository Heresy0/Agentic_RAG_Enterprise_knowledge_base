"""Consolidation must preserve gold bytes, group scores and historical compatibility."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from backend.app.evaluation.scoring_rules import apply_scoring_rules
from backend.app.evaluation.suite import load_suite, select_groups
from scripts import evaluate
from scripts.build_evaluation_suite import CURRENT, ROOT, build_artifacts, verify_archives
from scripts.evaluate_answers import load_dataset
from scripts.evaluate_system import main, parse_args
from scripts.evaluate_saved_ragas import apply_reference_overlay, main as ragas_main


def test_current_pack_is_reproducible_and_preserves_every_source_row_and_rule():
    artifacts,suite=build_artifacts()
    assert all(p.read_bytes()==raw for p,raw in artifacts.items())
    assert verify_archives()['files']
    cases=load_dataset(CURRENT/'single_turn.jsonl')
    assert len(cases)==len({c['id'] for c in cases})==120
    assert sum(c['answerable'] for c in cases)==104
    assert [g['cases'] for g in suite['groups']]==[100,12,8]
    effective=apply_scoring_rules(cases,CURRENT/'scoring_rules.json')
    for group in suite['groups']:
        source=ROOT/group['source'];original=load_dataset(source)
        assert select_groups(cases,suite,[group['id']])==original
        assert (CURRENT/'groups'/group['id']/'single_turn.jsonl').read_bytes()==source.read_bytes()
        previous=apply_scoring_rules(original,source.parent/'scoring_rules.json')
        current=select_groups(effective,suite,[group['id']])
        for old,new in zip(previous,current):
            if old['id']=='CC-005':
                assert {k:v for k,v in new.items() if k not in ('fact_alternatives','fact_assertions')}==old
            else:assert new==old
    assert (CURRENT/'scoring_rules.json').read_bytes()==(ROOT/'evals/scoring_rules.json').read_bytes()
    assert (CURRENT/'groups/main/scoring_rules.json').read_bytes()==(CURRENT/'scoring_rules.json').read_bytes()
    for name in ('conversations.json','permissions.json'):
        assert (CURRENT/name).read_bytes()==(ROOT/'evals/archive/xinghai_v4'/name).read_bytes()
    assert suite['conversations']['turns']==24 and suite['security']['cases']==12
    assert suite['independent_case_units']==140 and suite['check_steps']==156
    assert (CURRENT/'load_workload.jsonl').read_bytes()==(ROOT/'evals/archive/load/current_workload.jsonl').read_bytes()


def test_default_and_group_selection_never_silently_change_case_membership():
    assert parse_args([]).dataset_dir==CURRENT
    cases=load_dataset(CURRENT/'single_turn.jsonl');suite=load_suite(CURRENT,cases)
    assert len(select_groups(cases,suite,['multihop','completeness']))==20
    assert len(select_groups(cases,suite,['main','main']))==100
    with pytest.raises(ValueError,match='Unknown group'):select_groups(cases,suite,['unknown'])
    with pytest.raises(ValueError,match='suite.json'):select_groups(cases,None,['main'])


@pytest.mark.parametrize('fault',['dataset','rules','partition','conversations'])
def test_suite_rejects_fingerprint_drift_or_overlapping_groups(tmp_path,fault):
    shutil.copytree(CURRENT,tmp_path/'current');directory=tmp_path/'current'
    if fault in ('dataset','rules','conversations'):
        target={'dataset':'single_turn.jsonl','rules':'scoring_rules.json','conversations':'conversations.json'}[fault]
        with (directory/target).open('ab') as f:f.write(b'\n')
    else:
        path=directory/'suite.json';s=json.loads(path.read_text(encoding='utf-8'))
        s['groups'][1]['case_ids'][0]=s['groups'][0]['case_ids'][0]
        path.write_text(json.dumps(s),encoding='utf-8')
    with pytest.raises(ValueError):load_suite(directory,load_dataset(CURRENT/'single_turn.jsonl'))


def test_archive_guard_rejects_changed_gold(tmp_path):
    shutil.copytree(ROOT/'evals/archive',tmp_path/'evals/archive')
    with (tmp_path/'evals/archive/xinghai_v4/single_turn.jsonl').open('ab') as f:f.write(b'\n')
    with pytest.raises(ValueError,match='Frozen archive changed'):verify_archives(tmp_path)


def test_current_reference_adapter_preserves_gold_prose_while_rebinding_r10():
    dataset=CURRENT/'groups/main/single_turn.jsonl'
    original=json.loads((ROOT/'evals/archive/xinghai_v4/ragas_references_v1.json').read_text(encoding='utf-8'))
    samples=[dict(id=c['id'],reference=c['reference_answer']) for c in load_dataset(dataset)]
    rows,overlay=apply_reference_overlay(samples,dataset,dataset.parent/'scoring_rules.json',dataset.parent/'ragas_references_v1.json')
    assert len(rows)==100
    assert [{k:v for k,v in row.items() if k!='original_criteria_sha256'} for row in overlay['cases']]==[
        {k:v for k,v in row.items() if k!='original_criteria_sha256'} for row in original['cases']]
    assert [new['id'] for old,new in zip(original['cases'],overlay['cases'])
            if old['original_criteria_sha256']!=new['original_criteria_sha256']]==['CC-005']


def trace_for(case):
    rows=[dict(document_name=e['document'],content=e['text'],citation_id=f'资料{i}')
          for i,e in enumerate(case['expected_evidence'],1)]
    response=dict(answerable=case['answerable'],answer=case['reference_answer'],
                  citations=[dict(document_name=r['document_name'],citation_id=r['citation_id']) for r in rows])
    return dict(id=case['id'],question_hash=hashlib.sha256(case['question'].encode()).hexdigest(),
                response=response,stages={stage:rows for stage in ('vector','keyword','fusion','rerank','context')})


def test_replay_reports_all_groups_without_counting_missing_observations_as_pass(tmp_path):
    trace=trace_for(load_dataset(CURRENT/'single_turn.jsonl')[0]);path=tmp_path/'trace.jsonl'
    path.write_text(json.dumps(trace,ensure_ascii=False)+'\n',encoding='utf-8')
    output=tmp_path/'report'
    assert main(['--mode','replay','--layers','retrieval,rerank,context,answer','--trace-input',str(path),
                 '--output-dir',str(output),'--strict'])==2
    report=json.loads((output/'report.json').read_text(encoding='utf-8'))
    assert report['selected_cases']==120
    assert report['group_scores']['main']['layers']['answer']['summary']['passed']==1
    assert report['group_scores']['main']['layers']['answer']['summary']['skipped']==99
    assert report['group_scores']['multihop']['layers']['answer']['summary']['skipped']==12
    assert report['group_scores']['completeness']['layers']['answer']['summary']['skipped']==8
    assert '单轮分组评分' in (output/'report.md').read_text(encoding='utf-8')
    selected=tmp_path/'selected'
    main(['--mode','replay','--layers','answer','--group','main','--trace-input',str(path),'--output-dir',str(selected)])
    assert list(json.loads((selected/'report.json').read_text(encoding='utf-8'))['group_scores'])==['main']
    with pytest.raises(ValueError,match='Unknown single-turn'):
        main(['--layers','answer','--group','multihop','--case-ids','XH-001','--output-dir',str(tmp_path/'bad')])


def test_public_entry_dispatches_without_enabling_model_calls(monkeypatch):
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        return type('Result',(),dict(returncode=0))()
    monkeypatch.setattr(evaluate.subprocess,'run',run)
    assert evaluate.main(['check'])==0 and calls[-1][-1]=='--check'
    assert evaluate.main(['--mode','offline','--layers','answer'])==0
    assert calls[-1][1].endswith('evaluate_system.py') and '--allow-model-calls' not in calls[-1]
    assert evaluate.main(['load','--dry-run'])==0 and calls[-1][-1]=='--dry-run'


def test_saved_ragas_dry_run_accepts_new_pack_without_changing_old_100_contract(tmp_path):
    cases=load_dataset(CURRENT/'single_turn.jsonl');traces=[trace_for(c) for c in cases]
    # Some negative gold has no positive contexts; use an actual-input fixture row for adapter shape testing.
    for t in traces:
        if not t['stages']['context']:t['stages']['context']=[dict(document_name='未提供事实',content='资料不足',citation_id='资料1')]
    source=tmp_path/'source.json';source.write_text(json.dumps(dict(dataset_sha256=hashlib.sha256((CURRENT/'single_turn.jsonl').read_bytes()).hexdigest(),
        scorer_version='2026-10-16-r9',layers=dict(answer=dict(records=[dict(id=t['id'],status='passed',response=t['response']) for t in traces])))),encoding='utf-8')
    path=tmp_path/'traces.jsonl';path.write_text(''.join(json.dumps(t,ensure_ascii=False)+'\n' for t in traces),encoding='utf-8')
    out=tmp_path/'ragas'
    assert ragas_main(['--dataset',str(CURRENT/'single_turn.jsonl'),'--source-report',str(source),
        '--traces',str(path),'--output-dir',str(out),'--dry-run'])==0
    assert json.loads((out/'dry-run.json').read_text(encoding='utf-8'))['samples']==120
