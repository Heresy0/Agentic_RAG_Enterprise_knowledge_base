"""Optional OCR candidate preserves the default and never guesses text."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.documents.pdf_splitter import _run_ocr


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.delenv('PDF_OCR_DPI',raising=False)
    monkeypatch.delenv('PDF_OCR_RETRY_EMPTY',raising=False)
    monkeypatch.setenv('PDF_OCR_ENABLED','true')
    monkeypatch.setenv('PDF_OCR_LANGUAGE','chi_sim+eng')
    rendered=[];calls=[];answers=[]
    def convert(*args,**kwargs):
        rendered.append(kwargs);return ['rendered-page']
    def recognize(image,**kwargs):
        calls.append(kwargs);return answers.pop(0)
    monkeypatch.setitem(sys.modules,'pdf2image',SimpleNamespace(convert_from_path=convert))
    monkeypatch.setitem(sys.modules,'pytesseract',SimpleNamespace(
        image_to_string=recognize,pytesseract=SimpleNamespace(tesseract_cmd='')))
    return rendered,calls,answers


def test_default_keeps_220_dpi_and_no_empty_retry(runtime):
    rendered,calls,answers=runtime;answers.append('')
    assert _run_ocr(Path('scan.pdf'),2,ocr_page=None)==''
    assert rendered[0]['dpi']==220 and len(calls)==1
    assert calls[0]=={'lang':'chi_sim+eng'}


def test_candidate_retries_empty_output_once_on_the_same_render(runtime,monkeypatch):
    rendered,calls,answers=runtime;answers.extend([' \n','恢复正文 20±3℃'])
    monkeypatch.setenv('PDF_OCR_DPI','300')
    monkeypatch.setenv('PDF_OCR_RETRY_EMPTY','true')
    assert _run_ocr(Path('scan.pdf'),2,ocr_page=None)=='恢复正文 20±3℃'
    assert len(rendered)==1 and rendered[0]['dpi']==300
    assert calls==[{'lang':'chi_sim+eng'},{'lang':'chi_sim+eng','config':'--psm 6'}]


@pytest.mark.parametrize('text',['有效正文','2043 C','扫描全能王'])
def test_nonempty_low_quality_text_is_not_rewritten_or_retried(runtime,monkeypatch,text):
    _,calls,answers=runtime;answers.append(text)
    monkeypatch.setenv('PDF_OCR_RETRY_EMPTY','true')
    assert _run_ocr(Path('scan.pdf'),1,ocr_page=None)==text
    assert len(calls)==1


def test_second_empty_result_stays_empty_without_a_third_attempt(runtime,monkeypatch):
    _,calls,answers=runtime;answers.extend(['',''])
    monkeypatch.setenv('PDF_OCR_RETRY_EMPTY','true')
    assert _run_ocr(Path('scan.pdf'),1,ocr_page=None)==''
    assert len(calls)==2


@pytest.mark.parametrize('dpi',['bad','300.0','149','401','99999999'])
def test_invalid_dpi_is_rejected_before_rendering(runtime,monkeypatch,dpi):
    rendered,calls,_=runtime
    monkeypatch.setenv('PDF_OCR_DPI',dpi)
    with pytest.raises(ValueError,match='PDF_OCR_DPI'):
        _run_ocr(Path('scan.pdf'),1,ocr_page=None)
    assert not rendered and not calls


def test_experiment_freezes_independent_originals_and_keeps_legacy_labels_separate():
    from scripts.run_ocr_small_experiment import frozen_inputs
    plan,samples=frozen_inputs()
    assert plan['profiles']=={'baseline':{'dpi':220,'retry_empty':False},'candidate':{'dpi':300,'retry_empty':True}}
    assert len(samples)==21
    checks=[c for sample in samples for c in sample['checks']]
    assert len(checks)==82 and len({(s['id'],c['id']) for s in samples for c in s['checks']})==82
    assert sum(len(s['checks']) for s in samples if s['group']=='independent')==27
    assert sum(bool(s['reference_excerpt']) for s in samples)==3
    assert all(s['visual_review']['ocr_input_rotation_degrees']==0 for s in samples if s['group']=='independent')


def test_excerpt_metric_penalizes_numeric_symbols_and_does_not_claim_full_page_cer():
    from scripts.run_ocr_small_experiment import excerpt_distance
    assert excerpt_distance('20±3℃','页眉 温度20±3℃ 后续正文')==(0,6)
    assert excerpt_distance('20±3℃','2043C')[0]>0
    assert excerpt_distance('20±3℃','')==(6,6)


def test_experiment_cannot_overwrite_old_reports_or_frozen_inputs(tmp_path):
    from scripts.run_ocr_small_experiment import guard_output,ROOT
    with pytest.raises(ValueError): guard_output(ROOT/'evals/manufacturing_zh_v3/reports/old.json')
    with pytest.raises(ValueError): guard_output(ROOT/'sample_docs/ocr_independent_v1/documents/source.json')
    existing=ROOT/'tmp/ocr-small-experiment-v1/occupied-test.json'
    existing.parent.mkdir(parents=True,exist_ok=True)
    try:
        existing.write_text('{}')
        with pytest.raises(ValueError,match='never overwritten'): guard_output(existing)
    finally: existing.unlink()
