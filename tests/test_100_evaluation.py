"""Protect legacy observations and validate the new 100-case evidence contract."""
import hashlib
import json
from pathlib import Path
import pytest
from backend.app.documents.policy_metadata import extract_policy
from backend.app.documents.text_splitter import split_markdown
from backend.app.evaluation.metrics import evidence_metrics, score_answer
from backend.app.evaluation.offline import evaluate_parsing
from scripts.build_100_evaluation import CORPUS, DATASET, DELTA, LEGACY, UNIFIED, build_artifacts, prepare_corpus, validate_parsed
from scripts.build_knowledge_expansion import write_artifacts
from scripts.corpus_100_data import make_cases
from scripts.evaluate_answers import load_dataset
from scripts.evaluate_system import parse_args

ROOT=Path(__file__).resolve().parents[1]

def test_default_pack_has_100_cases_and_preserves_all_legacy_bytes():
    args=parse_args([])
    assert args.dataset_dir==DATASET and args.corpus==UNIFIED
    old=(LEGACY/'single_turn.jsonl').read_bytes()
    assert (DATASET/'single_turn.jsonl').read_bytes().startswith(old)
    rows=load_dataset(DATASET/'single_turn.jsonl')
    assert len(rows)==len({r['id'] for r in rows})==100
    assert rows[:80]==load_dataset(LEGACY/'single_turn.jsonl')
    assert sum(r['answerable'] for r in rows)==87
    for name in ('conversations.json','permissions.json'):
        assert (DATASET/name).read_bytes()==(LEGACY/name).read_bytes()


def test_checked_in_generation_is_reproducible_and_read_only():
    artifacts=build_artifacts()
    before={p:p.stat().st_mtime_ns for p in artifacts}
    assert write_artifacts(artifacts,check=True)==0
    assert write_artifacts(artifacts)==0
    assert before=={p:p.stat().st_mtime_ns for p in artifacts}


def test_real_parser_keeps_every_new_anchor_and_no_answers_in_ingestion():
    assert validate_parsed()>0
    result=evaluate_parsing(CORPUS,make_cases())
    assert all(r['status']=='passed' for r in result['records'])
    assert sum(r['kind']=='document' for r in result['records'])==12
    assert sum(r['kind']=='evidence' for r in result['records'])==16
    manifest=json.loads((CORPUS/'manifest.json').read_text(encoding='utf-8'))
    expected={CORPUS/r['file'] for r in manifest['documents']}
    assert expected=={p for p in (CORPUS/'documents').rglob('*') if p.is_file()}
    for row in manifest['documents']:
        raw=(CORPUS/row['file']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==row['sha256']
        assert 'reference_answer' not in raw.decode('utf-8')
    assert not any(p.name in ('single_turn.jsonl','答案与评分.md','README.md') for p in expected)


@pytest.mark.parametrize('case',make_cases(),ids=lambda c:c['id'])
def test_labelled_reference_is_accepted_and_negatives_do_not_get_fake_evidence(case):
    response=dict(answerable=case['answerable'],answer=case['reference_answer'],
                  citations=[dict(document_name=n) for n in case['required_source_documents']])
    assert score_answer(case,response)['automatic_proxy_pass']
    if not case['answerable']:
        assert not case['required_facts'] and not case['expected_evidence'] and case['diagnostic_evidence']
        assert not score_answer(case,dict(answerable=True,answer='2026年10月20日。',citations=[]))['automatic_proxy_pass']


def test_cross_scope_case_requires_all_three_sources_not_just_public_routing():
    case=next(c for c in make_cases() if c['id']=='NQ-010')
    assert set(case['source_groups'])=={'公司公共','技术部'}
    for omitted in case['required_source_documents']:
        citations=[dict(document_name=n) for n in case['required_source_documents'] if n!=omitted]
        assert not score_answer(case,dict(answerable=True,answer=case['reference_answer'],citations=citations))['automatic_proxy_pass']
    public=(CORPUS/'documents/01_公司公共/57_南栖项目范围与资料路由.md')
    rows=[dict(document_name=public.name,content=c.page_content) for c in split_markdown(public)]
    assert not evidence_metrics(case['expected_evidence'],rows)['all_evidence_present']


def test_history_and_current_versions_keep_disjoint_effective_dates():
    manifest=json.loads((CORPUS/'manifest.json').read_text(encoding='utf-8'))
    pair=sorted((r for r in manifest['documents'] if r['document_code']=='XH-NQ-REST'),key=lambda r:r['effective_from'])
    assert len(pair)==2 and pair[0]['effective_to']<pair[1]['effective_from']
    for row in pair:
        path=CORPUS/row['file']
        text='\n'.join(c.page_content for c in split_markdown(path))
        policy=extract_policy(text,path.name)
        assert policy['business_version']==row['business_version']
        assert policy['effective_from']==row['effective_from']
    assert '10小时' in (CORPUS/pair[0]['file']).read_text(encoding='utf-8')
    assert '8小时' in (CORPUS/pair[1]['file']).read_text(encoding='utf-8')


def test_prepare_corpus_has_only_68_exact_sources_and_rejects_answer_contamination(tmp_path):
    out=tmp_path/'combined'
    manifest=prepare_corpus(output=out)
    assert manifest['total_documents']==68
    for row in manifest['documents']:
        assert hashlib.sha256((out/row['file']).read_bytes()).hexdigest()==row['sha256']
    original={p:p.read_bytes() for p in out.rglob('*') if p.is_file()}
    prepare_corpus(output=out)
    assert original=={p:p.read_bytes() for p in out.rglob('*') if p.is_file()}
    (out/'documents/answers.md').write_text('评估答案不得入库',encoding='utf-8')
    with pytest.raises(ValueError,match='Unexpected files'):
        prepare_corpus(output=out)
    with pytest.raises(ValueError,match='overlaps'):
        prepare_corpus(output=CORPUS/'generated')


def test_delta_is_separate_from_inherited_conversations_and_security():
    delta=json.loads((DELTA/'coverage.json').read_text(encoding='utf-8'))
    assert delta['single_turn_cases']==20 and delta['answerable']==16 and delta['unanswerable']==4
    assert sum(delta['categories'].values())==20 and delta['as_of_by_suite']=={'NQ':'2026-10-16'}
    assert delta['inherited_conversations']==delta['inherited_security_cases']==0
    assert not (DELTA/'conversations.json').exists() and not (DELTA/'permissions.json').exists()
