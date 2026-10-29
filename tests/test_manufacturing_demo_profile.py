import hashlib,json
from pathlib import Path
import pytest
from backend.app.evaluation.scoring_rules import apply_scoring_rules
from backend.app.evaluation.metrics import score_answer
from backend.app.evaluation.assertions import fact_assertion_match
from scripts.build_manufacturing_demo_profile import load_profile,OCR_PASSED,OCR_FAILURES

ROOT=Path(__file__).resolve().parents[1];PACK=ROOT/'evals/manufacturing_zh_lite_v1'

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
