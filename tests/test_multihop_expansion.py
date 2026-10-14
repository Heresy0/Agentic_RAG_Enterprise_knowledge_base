"""Annotation and source consistency only; no retrieval, models, HTTP or DB."""
from collections import Counter
from datetime import datetime
import hashlib
import json
import re

import pytest

from backend.app.documents.text_splitter import split_markdown
from backend.app.evaluation.metrics import score_answer
from scripts import build_multihop_expansion as pack
from scripts.evaluate_answers import load_dataset


@pytest.fixture(scope='module')
def cases():
    return load_dataset(pack.DATASET / 'single_turn.jsonl')


@pytest.fixture(scope='module')
def context():
    rows = []
    for number, *_ in pack.DOCUMENTS:
        path = pack.CORPUS / pack.source_path(number)
        for chunk in split_markdown(path):
            rows.append(dict(document_name=path.name, content=chunk.page_content,
                             citation_id=f'c{len(rows)+1}'))
    return rows


@pytest.mark.parametrize('index', range(12))
def test_reference_annotations_work_with_existing_proxy_scorer(cases, context, index):
    case = cases[index]
    # Deliberately supply all annotated source chunks, not a simulated retrieval.
    selected = [row for row in context if row['document_name'] in case['required_source_documents']]
    citations = [dict(document_name=row['document_name'], citation_id=row['citation_id']) for row in selected]
    response = dict(answer=case['reference_answer'], answerable=case['answerable'], citations=citations)
    result = score_answer(case, response, selected)
    assert result['automatic_proxy_pass'], result
    assert result['semantic_correctness'] == 'requires_manual_review'


def test_dependency_graph_and_boundaries_do_not_leak_answers(cases):
    assert Counter(c['case_kind'] for c in cases) == dict(two_hop=4, three_hop=2, complex=3, boundary=3)
    assert sum(c['answerable'] for c in cases) == 9
    for case in cases:
        assert all(entity not in case['question'] for entity in case['intermediate_entities'])
        assert len(case['hop_steps']) == case['hop_count']
        for step in case['hop_steps']:
            assert all(0 < dep < step['index'] for dep in step['depends_on'])
        if not case['answerable']:
            assert not case['expected_evidence'] and not case['required_source_documents']
            assert case['diagnostic_evidence']
    bodies = '\n'.join((pack.CORPUS / pack.source_path(n)).read_text(encoding='utf-8') for n, *_ in pack.DOCUMENTS)
    assert all(c['question'] not in bodies for c in cases)
    assert not any(label in bodies for label in ('MH-001', '参考答案', '测试题册'))


@pytest.mark.parametrize('tamper', ['primary_quote', 'supporting_quote', 'entity_leak'])
def test_generator_rejects_unanchored_labels_and_revealed_intermediate_codes(monkeypatch, tamper):
    rows = pack.make_cases()
    if tamper == 'primary_quote':
        rows[0]['hop_steps'][0]['source_quote'] = '不存在的原文'
    elif tamper == 'supporting_quote':
        rows[3]['hop_steps'][0]['supporting_quotes'] = ['未经客户同意也可以签署']
    else:
        rows[0]['question'] += 'AR-T2'
    monkeypatch.setattr(pack, 'make_cases', lambda: rows)
    with pytest.raises(ValueError, match='Evidence not in source|Intermediate entity leaked'):
        pack.artifacts()


def test_reversed_comparison_is_not_accepted_with_all_numeric_keywords(cases, context):
    case = next(c for c in cases if c['id'] == 'MH-009')
    rows = [row for row in context if row['document_name'] in case['required_source_documents']]
    answer = case['reference_answer'] + '因此两个事件均达标。'
    result = score_answer(case, dict(answer=answer, answerable=True,
        citations=[dict(document_name=r['document_name'], citation_id=r['citation_id']) for r in rows]), rows)
    assert not result['automatic_proxy_pass']
    assert result['contradictory_facts']['未达标']


def test_elapsed_times_are_recomputed_from_source_timestamps():
    body = (pack.CORPUS / pack.source_path(56)).read_text(encoding='utf-8')
    records = re.findall(r'INC-2026-201[78]于([^\n]+)', body)
    assert len(records) == 2
    expected = [(470, 1920, 480, 2100), (1080, 2460, 900, 3600)]
    rules = (pack.CORPUS / pack.source_path(55)).read_text(encoding='utf-8')
    for line, (first, restore, first_limit, restore_limit), grade in zip(records, expected, ('SL-B2', 'SL-C1')):
        stamps = re.findall(r'\d{2}:\d{2}(?::\d{2})?', line)
        assert len(stamps) == 4
        times = [datetime.strptime(s if len(s) == 8 else s+':00', '%H:%M:%S') for s in stamps]
        assert (times[2]-times[0]).total_seconds() == first
        assert (times[3]-times[1]).total_seconds() == restore
        assert f'{grade}的首响应时限为{first_limit//60}分钟，恢复时限为{restore_limit//60}分钟' in rules
    assert expected[0][0] <= expected[0][2] and expected[0][1] <= expected[0][3]
    assert expected[1][0] > expected[1][2] and expected[1][1] <= expected[1][3]


def test_reproducibility_preserves_original_dataset_and_corpus():
    originals = [*pack.ROOT.joinpath('evals/xinghai_v3').rglob('*'),
                 pack.ROOT/'sample_docs/xinghai_base/manifest.json',
                 pack.ROOT/'sample_docs/xinghai_expansion/manifest.json']
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in originals if p.is_file()}
    pack.build(check=True)
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}
    coverage = json.loads((pack.DATASET/'coverage.json').read_text(encoding='utf-8'))
    assert all(row['all_evidence_present'] for row in coverage['evidence_checks'])
    assert coverage['live_answer_evaluation'] == 'not_run'


def test_prepare_corpus_rejects_contamination_without_removing_it(tmp_path, monkeypatch):
    output = tmp_path/'corpus'
    monkeypatch.setattr(pack, 'UNIFIED', output)
    output.mkdir()
    sentinel = output/'answers.md'
    sentinel.write_text('参考答案不得入库', encoding='utf-8')
    with pytest.raises(ValueError, match='Unexpected files'):
        pack.prepare_corpus(output)
    assert sentinel.read_text(encoding='utf-8') == '参考答案不得入库'
    sentinel.unlink()
    manifest = pack.prepare_corpus(output)
    assert manifest['total_documents'] == 56
    assert manifest['group_counts']['技术部'] >= 2 and manifest['group_counts']['平台研发部'] >= 4
    for doc in manifest['documents']:
        assert hashlib.sha256((output/doc['file']).read_bytes()).hexdigest() == doc['sha256']
    assert len(list((output/'documents').rglob('*.*'))) == 56
