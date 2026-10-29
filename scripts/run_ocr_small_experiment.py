"""Two frozen, local OCR profiles. No DB, retrieval, generation or network calls."""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.metadata
import json
import os
from pathlib import Path
import sys
from time import perf_counter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.build_manufacturing_public import dump,sha
from scripts.evaluate_answers import normalize

EVAL=ROOT/'evals/ocr_small_experiment_v1'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def frozen_inputs():
    plan=read(EVAL/'plan.json')
    for path,key in [(EVAL/'annotations.json','annotations_sha256'),
                     (ROOT/'sample_docs/ocr_independent_v1/manifest.json','source_manifest_sha256'),
                     (ROOT/'evals/manufacturing_zh_v3/annotations.json','original_v3_annotations_sha256')]:
        if sha(path.read_bytes())!=plan[key]: raise ValueError('Frozen experiment changed')
    samples=read(EVAL/'annotations.json')['samples']
    for sample in samples:
        path=(ROOT/sample['file']).resolve()
        if not any(path.is_relative_to((ROOT/folder).resolve()) for folder in
                   ('sample_docs/ocr_independent_v1/documents','sample_docs/manufacturing_zh_v3/documents')):
            raise ValueError('OCR source outside reviewed document roots')
        if sha(path.read_bytes())!=sample['source_sha256']: raise ValueError('Original PDF changed')
    return plan,samples


def guard_output(path):
    path=path.resolve()
    if not (path.is_relative_to((ROOT/'tmp').resolve()) or path.is_relative_to((EVAL/'reports').resolve())):
        raise ValueError('Results must stay in tmp or the new experiment reports')
    if path.exists(): raise ValueError('Existing results are never overwritten')


def run(profile,output):
    guard_output(output)
    plan,samples=frozen_inputs()
    settings=plan['profiles'][profile]
    import pytesseract
    from backend.app.documents.pdf_splitter import _run_ocr
    from scripts.validate_manufacturing_public import parser_fingerprint
    values={'PDF_OCR_DPI':str(settings['dpi']),'PDF_OCR_RETRY_EMPTY':str(settings['retry_empty']).lower(),
            'PDF_OCR_LANGUAGE':plan['language'],'PDF_OCR_ENABLED':'true'}
    previous={k:os.environ.get(k) for k in values}
    original=pytesseract.image_to_string
    attempts=[]
    def observed(image,**kwargs):
        start=perf_counter()
        text=original(image,**kwargs)
        attempts.append(dict(config=kwargs.get('config','default_psm3'),seconds=perf_counter()-start,
                             text_sha256=sha(text.encode()),characters=len(text.strip()),text=text))
        return text
    records=[]
    metadata=dict(profile=profile,settings=settings,language=plan['language'],
        plan_sha256=sha((EVAL/'plan.json').read_bytes()),parser_sha256=parser_fingerprint(),
        harness_sha256=sha(Path(__file__).read_bytes()),external_model_calls=0,
        runtime=dict(tesseract=str(pytesseract.get_tesseract_version()),languages=pytesseract.get_languages(config=''),
            packages={p:importlib.metadata.version(p) for p in ('pytesseract','pdf2image','pypdf','pdfplumber')}))
    try:
        os.environ.update(values)
        pytesseract.image_to_string=observed
        for sample in samples:
            attempts.clear();start=perf_counter()
            try:
                text=_run_ocr(ROOT/sample['file'],sample['page'],ocr_page=None)
                record=dict(id=sample['id'],group=sample['group'],source_sha256=sample['source_sha256'],
                            page=sample['page'],text=text,empty=not text.strip(),
                            seconds=perf_counter()-start,attempts=list(attempts),error=None)
            except Exception as exc:
                record=dict(id=sample['id'],group=sample['group'],source_sha256=sample['source_sha256'],
                            page=sample['page'],text='',empty=True,seconds=perf_counter()-start,
                            attempts=list(attempts),error=type(exc).__name__)
            records.append(record)
            print(json.dumps(dict(profile=profile,id=record['id'],empty=record['empty'],
                                  attempts=len(record['attempts']),error=record['error'])),flush=True)
    finally:
        pytesseract.image_to_string=original
        for k,v in previous.items():
            if v is None: os.environ.pop(k,None)
            else: os.environ[k]=v
    dump(output,{**metadata,'records':records})
    return int(any(r['error'] for r in records))


def excerpt_distance(reference,text):
    """Minimum character edit distance to a substring, for a reviewed excerpt.

    Evaluation only. It does not select or rewrite an OCR output. This is NOT
    whole-page CER; the excerpt's surrounding page text is not penalized.
    """
    needle,haystack=normalize(reference),normalize(text)
    previous=[0]*(len(haystack)+1)
    for i,char in enumerate(needle,1):
        current=[i]
        for j,other in enumerate(haystack,1):
            current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(char!=other)))
        previous=current
    return min(previous),len(needle)


def score(baseline,candidate,output):
    guard_output(output)
    plan,samples=frozen_inputs()
    outcomes={}
    for name,path in [('baseline',baseline),('candidate',candidate)]:
        run_result=read(path)
        if (run_result['profile']!=name or run_result['settings']!=plan['profiles'][name]
                or run_result['plan_sha256']!=sha((EVAL/'plan.json').read_bytes())):
            raise ValueError('OCR observations do not match the frozen profile')
        if run_result['language']!=plan['language']: raise ValueError('OCR language differs')
        rows={r['id']:r for r in run_result['records']}
        if len(run_result['records'])!=len(samples) or len(rows)!=len(samples) or set(rows)!={s['id'] for s in samples}: raise ValueError('Missing OCR page observations')
        checked=[];excerpts=[]
        for sample in samples:
            record=rows[sample['id']]
            if record['source_sha256']!=sample['source_sha256'] or record['page']!=sample['page']:
                raise ValueError('OCR observation belongs to another source page')
            for check in sample['checks']:
                checked.append(dict(id=sample['id']+':'+check['id'],sample=sample['id'],group=sample['group'],kind=check['kind'],
                                    text=check['text'],matched=not record['error'] and normalize(check['text']) in normalize(record['text'])))
            if sample['reference_excerpt']:
                errors,size=excerpt_distance(sample['reference_excerpt'],record['text'])
                excerpts.append(dict(sample=sample['id'],edit_distance=errors,reference_characters=size,excerpt_error_rate=errors/size))
        totals={}
        for group in ('independent','legacy_regression'):
            group_checks=[r for r in checked if r['group']==group]
            group_pages=[r for r in rows.values() if r['group']==group]
            totals[group]=dict(checks=len(group_checks),matched=sum(r['matched'] for r in group_checks),
                pages=len(group_pages),empty_pages=sum(r['empty'] for r in group_pages),
                errors=sum(bool(r['error']) for r in group_pages),seconds=sum(r['seconds'] for r in group_pages),
                retries=sum(len(r['attempts'])>1 for r in group_pages))
        outcomes[name]=dict(totals=totals,checks=checked,excerpts=excerpts,
                            by_kind={kind:dict(total=sum(r['kind']==kind for r in checked),matched=sum(r['matched'] and r['kind']==kind for r in checked))
                                     for kind in sorted({r['kind'] for r in checked})},
                            raw_observations_sha256=sha(path.read_bytes()),runtime=run_result['runtime'],
                            parser_sha256=run_result['parser_sha256'])
    if (outcomes['baseline']['runtime']!=outcomes['candidate']['runtime']
            or outcomes['baseline']['parser_sha256']!=outcomes['candidate']['parser_sha256']):
        raise ValueError('The two profiles must use the same parser and OCR runtime')
    old={r['id']:r for r in outcomes['baseline']['checks']}
    new={r['id']:r for r in outcomes['candidate']['checks']}
    dump(output,dict(evaluation_type='sampled_ocr_page_text_only',external_model_calls=0,
        plan_sha256=sha((EVAL/'plan.json').read_bytes()),profiles=outcomes,
        improved=[k for k in old if not old[k]['matched'] and new[k]['matched']],
        regressed=[k for k in old if old[k]['matched'] and not new[k]['matched']],
        default_adoption=False,limitation='Only 21 sampled pages; anchors and excerpt distance do not certify whole pages, table relationships, or answer accuracy.'))
    print(json.dumps({name:value['totals'] for name,value in outcomes.items()},ensure_ascii=False))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    run_parser=sub.add_parser('run')
    run_parser.add_argument('--profile',choices=('baseline','candidate'),required=True)
    run_parser.add_argument('--output',type=Path,required=True)
    score_parser=sub.add_parser('score')
    score_parser.add_argument('--baseline',type=Path,required=True)
    score_parser.add_argument('--candidate',type=Path,required=True)
    score_parser.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.action=='run':return run(a.profile,a.output)
    score(a.baseline,a.candidate,a.output);return 0


if __name__=='__main__':raise SystemExit(main())
