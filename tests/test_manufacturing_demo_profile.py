import hashlib,json
from pathlib import Path
import pytest
from backend.app.evaluation.scoring_rules import apply_scoring_rules
from backend.app.evaluation.metrics import score_answer
from backend.app.evaluation.assertions import fact_assertion_match
from scripts.build_manufacturing_demo_profile import load_profile,OCR_PASSED,OCR_FAILURES

ROOT=Path(__file__).resolve().parents[1];PACK=ROOT/'evals/manufacturing_zh_lite_v1'


def test_demo_release_matches_unchanged_r13_criteria():
    from scripts.report_manufacturing_demo import check_demo_release
    release = check_demo_release(PACK)
    assert release['scorer_version'] == '2026-10-29-r13'
    assert release['stable']['source_commit'].startswith('43aeafc')
    assert release['reference_result']['reviewed_proxy_passed'] == 84


def test_demo_release_rejects_criterion_drift_but_accepts_checkout_newlines(tmp_path, monkeypatch):
    from scripts import report_manufacturing_demo as report
    monkeypatch.setattr(report, 'ROOT', tmp_path)
    text = tmp_path/'criterion.txt'
    text.write_bytes(b'fixed\r\n')
    release = dict(scorer_version=report.SCORER_VERSION,
        files_sha256_lf={'criterion.txt': hashlib.sha256(b'fixed\n').hexdigest()})
    (tmp_path/'demo_release.json').write_text(json.dumps(release), encoding='utf-8')
    assert report.check_demo_release(tmp_path) == release
    text.write_text('changed\n', encoding='utf-8')
    with pytest.raises(ValueError, match='criterion.txt'):
        report.check_demo_release(tmp_path)

def selected():
    return [json.loads(line)for split in ('development','regression')for line in (PACK/split/'single_turn.jsonl').read_text(encoding='utf-8').splitlines()]

def cases():return {c['id']:c for c in apply_scoring_rules(selected(),PACK/'scoring_rules.json')}

def result(case,text):return dict(answer=text,answerable=True,citations=[dict(document_name=name)for name in case['required_source_documents']])

def test_selection_keeps_every_non_ocr_case_and_exact_eight_original_ocr_cases():
    profile=load_profile(PACK/'profile.json');rows=selected()
    assert len(rows)==98 and len(profile['documents'])==16
    assert [s['count']for s in profile['splits'].values()]==[40,58]
    original=[json.loads(line)for split in ('development','test')for line in (ROOT/'evals/manufacturing_zh_v3'/split/'single_turn.jsonl').read_text(encoding='utf-8').splitlines()]
    ocr_names={Path(d['file']).name for d in profile['documents']if d['id']in ('MFG-ZH12','MFG-ZH14','MFG-ZH15','MFG-ZH16')}
    selected_ids={r['id']for r in rows};all_original={r['id']:r for r in original}
    for row in rows:assert row==all_original[row['id']]
    corpus=json.loads((ROOT/'sample_docs/manufacturing_zh_v3/manifest.json').read_text(encoding='utf-8'))
    all_ocr={Path(d['file']).name for d in corpus['documents']if 'OCR必需'in d['features']}
    assert {c['id']for c in original if not set(c['required_source_documents'])&all_ocr}<=selected_ids
    assert {c['id']for c in rows if set(c['required_source_documents'])&all_ocr}==set(OCR_PASSED)|set(OCR_FAILURES)
    assert len(ocr_names)==4

@pytest.mark.parametrize('number,text',[
    (14,'并非只能一物一码，可以选择一品一码、一批一码、一物一码。'),
    (27,'温度测量点为9个，相对湿度测量点为3个。'),
    (35,'至少5点，包括上限、下限和50%附近，均匀选取，可增加零点或依委托者要求。'),
    (46,'适用范围不超过(-2500~2500)Pa。'),
    (81,'无需拔下定位针，测量与100mm的差值，取绝对值最大者。'),
    (44,'安装应保持平行，重复测量3次。'),
    (84,'校准点不包括零点。'),
])
def test_reviewed_equivalents_and_question_scope_keep_other_contracts(number,text):
    case=cases()[f'MFGZH-Q{number:03}'];response=result(case,text)
    assert score_answer(case,response)['automatic_proxy_pass']
    assert not score_answer(case,dict(response,citations=[]))['automatic_proxy_pass']
    assert not score_answer(case,response,[])['automatic_proxy_pass']

@pytest.mark.parametrize('number,text',[
    (14,'不是其他原因。只能一物一码；一品一码、一批一码、一物一码。'),
    (14,'不是并非只能一物一码。一品一码、一批一码、一物一码。'),
    (27,'19个温度测量点，3个相对湿度测量点。'),
    (35,'至少50点，包括上限、下限和50%附近，均匀选取，可增加零点或依委托者要求。'),
    (46,'适用范围不超过(-2500~2500)kPa。'),
    (81,'并非无需拔下定位针，测量与100mm的差值，取绝对值最大者。'),
    (44,'安装应保持平行，重复测量13次。'),
    (84,'校准点包含零点。'),
])
def test_review_never_accepts_changed_numbers_units_or_reversed_conclusions(number,text):
    case=cases()[f'MFGZH-Q{number:03}']
    assert not score_answer(case,result(case,text))['automatic_proxy_pass']

def test_question_binding_rejects_annotations_used_on_changed_question():
    rows=selected();next(c for c in rows if c['id']=='MFGZH-Q044')['question']='改成询问完整结果处理流程'
    with pytest.raises(ValueError,match='fingerprint'):apply_scoring_rules(rows,PACK/'scoring_rules.json')

def test_real_missing_preconditions_and_reference_scope_remain_required():
    cs=cases();case=cs['MFGZH-Q034']
    assert not score_answer(case,result(case,'不能立即校准，应放置4小时以上再通电。'))['automatic_proxy_pass']
    assert '闭环'in cs['MFGZH-Q018']['required_facts']
    assert '全过程'in cs['MFGZH-Q019']['required_facts']


@pytest.mark.parametrize('number', [2, 12, 20, 31, 49, 16, 21])
def test_reviewed_recorded_response_boundaries(number):
    prefix = 'N' if number in (16, 21) else 'Q'
    key = f'MFGZH-{prefix}{number:02}' if prefix == 'N' else f'MFGZH-Q{number:03}'
    recorded = json.loads((PACK/'reports/20261029-r1/answers.json').read_text(encoding='utf-8'))
    response = next(r['response'] for r in recorded if r['id'] == key)
    assert score_answer(cases()[key], response)['automatic_proxy_pass']
    if prefix == 'Q' or number == 21:
        assert not score_answer(cases()[key], dict(response, citations=[]))['automatic_proxy_pass']
        assert not score_answer(cases()[key], response, [])['automatic_proxy_pass']


@pytest.mark.parametrize('number,text', [
    (2, '也应当予以记录，实行零风险报告，但可以不留记录。'),
    (12, '不是必须填报，生产单位选择是否公开。'),
    (20, '无需定期评估，只需一次验收即可，报告最高管理者。'),
    (31, '不是不必然不适用本规范，部分测量和输出功能不适用。'),
    (49, '标准器、压差计、气源至少放置20小时，按厂家要求预热300分钟。'),
    (49, '标准器、压差计、气源至少放置2分钟，按厂家要求预热30小时。'),
])
def test_new_equivalents_keep_reversed_claims_and_wrong_units_failed(number, text):
    case = cases()[f'MFGZH-Q{number:03}']
    assert not score_answer(case, result(case, text))['automatic_proxy_pass']


def test_tolerance_case_accepts_refusal_or_complete_clarification_only():
    case = cases()['MFGZH-N16']
    refusal = dict(answer='缺少图纸，不能编造公差。', answerable=False,
                   response_type='refuse', citations=[])
    assert score_answer(case, refusal)['automatic_proxy_pass']
    clarify = dict(refusal, response_type='clarify', missing_fields=['批准图纸'],
                   clarification_questions=['请提供批准图纸。'])
    assert score_answer(case, clarify)['automatic_proxy_pass']
    assert not score_answer(case, dict(clarify, missing_fields=[]))['automatic_proxy_pass']
    assert not score_answer(case, dict(clarify, answerable=True))['automatic_proxy_pass']


def test_calibration_answer_must_deny_guarantee_and_keep_product_inspection_boundary():
    case = cases()['MFGZH-N21']
    for text in ('可以保证所有产品都100%合格。',
                 '不能保证其他情况，但可以保证所有产品都100%合格，不能替代产品检验。',
                 '不能保证所有产品都100%合格，但可以替代产品检验。',
                 '不能保证所有产品都100%合格。'):
        assert not score_answer(case, result(case, text))['automatic_proxy_pass']


def test_r12_rules_remain_available_without_new_boundary_overrides():
    old = {c['id']: c for c in apply_scoring_rules(selected(), PACK/'scoring_rules_r12.json')}
    assert old['MFGZH-N16']['expected_behavior'] == 'refuse'
    assert 'accepted_behaviors' not in old['MFGZH-N16']
    assert old['MFGZH-N21']['answerable'] is False
