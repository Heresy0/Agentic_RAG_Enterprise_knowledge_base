import json
from pathlib import Path

import pytest

from backend.app.evaluation.metrics import score_answer
from backend.app.evaluation.reporting import compare_baseline
from backend.app.evaluation.scoring_rules import apply_scoring_rules


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / 'evals/xinghai_v4'


def cases():
    rows = [json.loads(line) for line in (PACK/'single_turn.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    return {row['id']: row for row in apply_scoring_rules(rows, PACK/'scoring_rules.json')}


def response(case, text):
    return dict(answerable=True, answer=text, citations=[dict(document_name=name)
                for name in case['required_source_documents']])


@pytest.mark.parametrize('identifier,fact,text', [
    ('XH-D-29', '不能', '文档上传成功不等于可以检索'),
    ('XH-E-054', '不证明', '目标配置不能证明真实生产部署已启用'),
    ('XH-E-054', '不证明', '目标配置无法证明真实生产部署已启用'),
    ('NQ-011', '不能证明', '客户接受并不证明该缺陷已经修复'),
    ('NQ-011', '不能证明', '仅凭客户接受无法认定缺陷已修复'),
])
def test_reviewed_negatives_accept_equivalence_but_keep_evidence_and_other_facts(identifier, fact, text):
    case = dict(cases()[identifier], required_facts=[fact])
    r = response(case, text)
    assert score_answer(case, r)['automatic_proxy_pass']
    assert not score_answer(case, dict(r, citations=[]))['automatic_proxy_pass']
    assert not score_answer(dict(case, required_facts=[fact, '999元']), r)['automatic_proxy_pass']
    assert not score_answer(case, r, [])['automatic_proxy_pass']


@pytest.mark.parametrize('identifier,fact,text', [
    ('XH-D-29', '不能', '不能缺少日志，上传成功即可检索，failed文档可以进入候选'),
    ('XH-D-29', '不能', '上传成功不等于可以检索，但是上传成功即可检索'),
    ('XH-E-054', '不证明', '不是不能证明真实生产部署已启用'),
    ('XH-E-054', '不证明', '不能证明真实生产部署已启用，但也证明真实生产部署已启用'),
    ('NQ-011', '不能证明', '并非不能证明该缺陷已经修复'),
    ('NQ-011', '不能证明', '不能证明该缺陷已修复。但客户接受就证明该缺陷已经修复'),
])
def test_negation_reversals_and_conflicts_still_fail(identifier, fact, text):
    case = dict(cases()[identifier], required_facts=[fact])
    assert not score_answer(case, response(case, text))['automatic_proxy_pass']


@pytest.mark.parametrize('text', [
    '首响应不满足，恢复未满足',
    '首响应满足，恢复不满足',
    '不能认为首响应和恢复均满足',
    '首响应和恢复均满足。但是恢复超时',
])
def test_satisfied_must_not_match_unsatisfied_or_conflicting_claims(text):
    case = dict(cases()['NQ-014'], required_facts=['满足'])
    assert not score_answer(case, response(case, text))['automatic_proxy_pass']
    assert score_answer(case, response(case, '首响应与恢复均满足时限'))['automatic_proxy_pass']


def test_optional_details_are_not_required_but_core_omissions_still_fail():
    c = cases()
    assert score_answer(c['XH-031'], response(c['XH-031'], '新增跨部门审批代理，4人日，9月29日验收'))['automatic_proxy_pass']
    assert not score_answer(c['XH-031'], response(c['XH-031'], '4人日，9月29日验收'))['automatic_proxy_pass']
    assert score_answer(c['CC-012'], response(c['CC-012'], '客户端解析缓存，23分钟'))['automatic_proxy_pass']
    assert not score_answer(c['CC-012'], response(c['CC-012'], '业务查询缓存，23分钟'))['automatic_proxy_pass']
    assert not score_answer(c['CC-012'], response(c['CC-012'], '客户端解析缓存，123分钟'))['automatic_proxy_pass']
    assert not score_answer(c['XH-E-054'], response(c['XH-E-054'], '不能证明真实生产部署已启用'))['automatic_proxy_pass']


def test_alternative_evidence_requires_both_anchors_and_real_citation_identity():
    case = cases()['XH-022']
    source = '26_项目测试验收与缺陷分级规范.md'
    citation = dict(document_name=source, citation_id='资料1', chunk_id='real', parent_id='parent')
    r = dict(answerable=True, answer='计划2026年10月9日修复，该计划不代表已经修复', citations=[citation])
    row = dict(citation, content='计划2026年10月9日修复。该计划不代表已经修复。')
    assert score_answer(case, r, [row])['automatic_proxy_pass']
    assert not score_answer(case, r, [dict(row, content='计划2026年10月9日修复')])['automatic_proxy_pass']
    assert not score_answer(case, r, [dict(row, chunk_id='forged')])['automatic_proxy_pass']


def test_all_references_pass_and_annotations_cannot_change_questions(tmp_path):
    raw = [json.loads(line) for line in (PACK/'single_turn.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    before = json.dumps(raw, ensure_ascii=False)
    updated = cases()
    for original in raw:
        row = updated[original['id']]
        for key in ('question', 'answerable', 'reference_answer', 'profile', 'expected_scope', 'source_groups'):
            assert row.get(key) == original.get(key)
        r = response(row, row['reference_answer'])
        if not row['answerable']:
            r = dict(r, answerable=False, citations=[])
        assert score_answer(row, r)['automatic_proxy_pass'], row['id']
    apply_scoring_rules(raw, PACK/'scoring_rules.json')
    assert json.dumps(raw, ensure_ascii=False) == before
    bad = tmp_path/'rules.json'
    bad.write_text(json.dumps(dict(version='2026-10-16-r7', cases={'XH-022': {'question':'changed'}})))
    with pytest.raises(ValueError):
        apply_scoring_rules(raw, bad)


def test_baseline_rejects_different_scoring_versions_or_rules():
    report = dict(scorer_version='r7', scoring_rules_sha256='same', layers={})
    assert compare_baseline(dict(report, scorer_version='r6'), report)['status']=='incompatible'
    assert compare_baseline(dict(report, scoring_rules_sha256='changed'), report)['status']=='incompatible'


@pytest.mark.parametrize('text', [
    '重排失败时保留融合阶段的结果作为降级，仍保留授权过滤。',
    '重排失败时保留融合后的结果作为降级。',
    '若重排失败则保留融合阶段的结果作为降级。',
    '当重排失败时，系统会将融合阶段的结果作为降级方案保留。',
    '重排失败时将融合后的结果作为降级保留。',
])
def test_reviewed_fusion_paraphrases_preserve_core_fact_and_source_requirements(text):
    case=cases()['XH-E-053']
    r=response(case,text)
    assert score_answer(case,r)['automatic_proxy_pass']
    assert not score_answer(case,dict(r,citations=[]))['automatic_proxy_pass']
    assert not score_answer(case,r,[])['automatic_proxy_pass']
    assert not score_answer(case,response(case,text.replace('降级','备用')))['automatic_proxy_pass']


@pytest.mark.parametrize('text', [
    '重排失败时不保留融合阶段的结果，取消降级。',
    '重排失败时不会保留融合后的结果作为降级。',
    '重排失败时丢弃融合结果，降级为空结果。',
    '保留融合阶段的结果降级。但是丢弃融合阶段的结果。',
    '重排失败时不将融合阶段的结果作为降级方案保留。',
    '重排失败时不会将融合后的结果作为降级保留。',
    '重排失败时将融合阶段的结果不作为降级方案保留。',
    '重排失败时将融合阶段的结果作为降级方案丢弃。',
])
def test_fusion_paraphrase_does_not_accept_reversed_or_conflicting_behavior(text):
    case=cases()['XH-E-053']
    assert not score_answer(case,response(case,text))['automatic_proxy_pass']


@pytest.mark.parametrize('condition', [
    '如果不满足则需整改',
    '若恢复不满足时限，须复核',
    '如果恢复超时则应升级处理',
    '一旦首响应超时就必须通知负责人',
])
def test_current_success_is_not_contradicted_by_remediation_instructions(condition):
    case=cases()['NQ-014']
    text='首响应7分钟，目标8分钟；恢复28分钟，目标30分钟，两项均满足；'+condition+'。'
    assert score_answer(case,response(case,text))['automatic_proxy_pass']
    # The exact same instructions do not prove that this event actually passed.
    no_conclusion='首响应7分钟，目标8分钟；恢复28分钟，目标30分钟；'+condition+'。'
    assert not score_answer(case,response(case,no_conclusion))['automatic_proxy_pass']
    actual_failure=text+'但是本次恢复不满足。'
    assert not score_answer(case,response(case,actual_failure))['automatic_proxy_pass']


@pytest.mark.parametrize('text', [
    '两项均满足。如果不满足则本次恢复不满足。',
    '首响应满足，恢复不满足；如果不满足则需整改。',
    '如果不满足则需整改，否则两项均满足。',
    '两项均满足；恢复未满足，并不是整改预案。',
])
def test_only_bounded_remediation_templates_are_hypothetical(text):
    case=dict(cases()['NQ-014'],required_facts=['满足'])
    assert not score_answer(case,response(case,text))['automatic_proxy_pass']
