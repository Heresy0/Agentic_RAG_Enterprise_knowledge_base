"""Audit native Chinese sources, actual splitters and frozen evidence labels.

This command never connects to an application server, a DB, or a model. Full
page extraction is observed before marginalia removal; OCR is performed only by
the existing application extractor, without substituting gold-label text.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.build_manufacturing_public import dump,sha
from scripts.build_manufacturing_zh import baseline_fingerprints
from scripts.validate_manufacturing_public import inventory,parse,parser_fingerprint
from scripts.evaluate_answers import normalize,load_dataset


def audit_chunks(corpus,cache):
    manifest=inventory(corpus)
    records=[]
    for source in manifest['documents']:
        saved=json.loads((cache/(source['id']+'.json')).read_text(encoding='utf-8'))
        if saved['source_sha256']!=source['sha256'] or saved['parser_sha256']!=parser_fingerprint():
            raise ValueError('Stale real-parser cache')
        chunks=saved['chunks']
        invalid=[i for i,c in enumerate(chunks) if not c['content'].strip() or 'section_path' not in c['metadata']]
        if source['format']=='pdf':
            invalid += [i for i,c in enumerate(chunks) if not (1<=c['metadata'].get('page_start',0)<=c['metadata'].get('page_end',0)<=source['physical_pages'])]
        lengths=[len(c['content']) for c in chunks]
        records.append(dict(id=source['id'],status=saved['record']['status'],chunks=len(chunks),invalid_structure_indices=sorted(set(invalid)),
            chunk_types=dict(Counter(c['metadata'].get('chunk_type') for c in chunks)),
            ocr_chunks=sum('ocr' in c['metadata'].get('parser','') for c in chunks),
            review_required_chunks=sum(bool(c['metadata'].get('quality',{}).get('review_required')) for c in chunks),
            root_section_chunks=sum(not c['metadata'].get('section_path') for c in chunks),
            short_chunks_under_60=sum(n<60 for n in lengths),
            long_chunks_over_1200=sum(n>1200 for n in lengths),
            watermark_chunks=sum('扫描全能王' in c['content'] for c in chunks),
            replacement_characters=sum(c['content'].count('\ufffd') for c in chunks),
            limitation='结构、长度和水印检查，不等于全文语义或表格关系全部正确。'))
    return dict(evaluation_type='offline_actual_chunk_structure_and_noise_audit',external_model_calls=0,
                corpus_manifest_sha256=sha((corpus/'manifest.json').read_bytes()),parser_sha256=parser_fingerprint(),
                total_documents=len(records),total_chunks=sum(r['chunks'] for r in records),
                failed_documents=[r['id'] for r in records if r['invalid_structure_indices'] or r['status']!='passed'],records=records)


def parse_observed(corpus,cache,report,*,ocr,workers):
    from backend.app.documents import pdf_splitter
    manifest=inventory(corpus)
    by_file={(corpus/s['file']).resolve():s for s in manifest['documents']}
    original_extract=pdf_splitter.extract_pdf_pages
    page_folder=cache.parent/'pages'
    def observed(path,*,ocr_page=None):
        pages=original_extract(path,ocr_page=ocr_page)
        source=by_file[Path(path).resolve()]
        dump(page_folder/(source['id']+'.json'),dict(source_sha256=source['sha256'],parser_sha256=parser_fingerprint(),
             pages=[dict(number=p.number,text=p.text,parser=p.parser,table_count=len(p.tables)) for p in pages]))
        return pages
    pdf_splitter.extract_pdf_pages=observed
    try:
        result=parse(corpus,cache,ocr=ocr,workers=workers)
    finally:
        pdf_splitter.extract_pdf_pages=original_extract
    result['harness_sha256']=sha(Path(__file__).read_bytes())
    dump(report,result)
    quality=audit_chunks(corpus,cache) if all(r['status'] in ('passed','blocked') for r in result['records']) else None
    if quality: dump(report.with_name('chunk-audit.json'),quality)
    print(json.dumps({k:v for k,v in result.items() if k!='records'}),flush=True)
    return 1 if result['summary'].get('failed') or result['summary'].get('error') else 3 if result['summary'].get('blocked') else 2 if result['summary'].get('skipped') else 0


def validate_freeze(evaluation,corpus):
    manifest=inventory(corpus)
    frozen=json.loads((evaluation/'splits.json').read_text(encoding='utf-8'))
    for key,path in [('corpus_manifest_sha256',corpus/'manifest.json'),
                     ('source_lock_sha256',ROOT/'sample_docs/manufacturing_zh_v3.sources.json'),
                     ('annotations_sha256',evaluation/'annotations.json'),
                     ('visual_review_sha256',evaluation/'visual-review.json')]:
        if frozen[key]!=sha(path.read_bytes()): raise ValueError('Frozen source or annotation changed')
    all_cases=[]
    for split,size in [('development',40),('test',80)]:
        path=evaluation/split/'single_turn.jsonl'
        if frozen['dataset_sha256'][split]!=sha(path.read_bytes()): raise ValueError('Frozen question set changed')
        cases=load_dataset(path)
        if len(cases)!=size: raise ValueError('Split size differs')
        all_cases+=cases
    annotations=json.loads((evaluation/'annotations.json').read_text(encoding='utf-8'))
    if len(annotations)!=120 or {a['id'] for a in annotations}!={c['id'] for c in all_cases}:
        raise ValueError('Annotation inventory differs')
    if (len({c['id'] for c in all_cases})!=120 or len({normalize(c['question']) for c in all_cases})!=120
            or len({a['concept_id'] for a in annotations})!=120):
        raise ValueError('Duplicate question')
    families={}
    for source in manifest['documents']: families.setdefault(source['source_family'],set()).add(source['split'])
    if any(len(v)!=1 for v in families.values()): raise ValueError('Source family leaks across splits')
    documents={r['file']:r for r in manifest['documents']}
    by_id={a['id']:a for a in annotations}
    for split in ['development','test']:
        for case in load_dataset(evaluation/split/'single_turn.jsonl'):
            a=by_id[case['id']]
            if a['split']!=split or any(documents[f]['split']!=split for f in case['source_file_paths']):
                raise ValueError('Question crosses split')
            if case['answerable'] and not a['evidence_locators']: raise ValueError('Missing original evidence')
            if not case['answerable'] and case.get('expected_behavior') not in ('refuse','clarify'):
                raise ValueError('Negative behavior is not explicit')
            for locator in a['evidence_locators']:
                source=documents[locator['document']]
                if source['sha256']!=locator['source_sha256'] or source['id']!=locator['source_id']:
                    raise ValueError('Evidence points at a different original')
    return frozen,all_cases,annotations


def validate_labels(evaluation,corpus,cache):
    from scripts.build_manufacturing_zh_questions import original_evidence_matches
    frozen,cases,annotations=validate_freeze(evaluation,corpus)
    manifest=inventory(corpus)
    docs={d['id']:d for d in manifest['documents']}
    visual=json.loads((evaluation/'visual-review.json').read_text(encoding='utf-8'))
    matches,failures=[],[]
    parsed={}
    for source in manifest['documents']:
        saved=json.loads((cache/(source['id']+'.json')).read_text(encoding='utf-8'))
        if saved['source_sha256']!=source['sha256'] or saved['parser_sha256']!=parser_fingerprint():
            raise ValueError('Stale parse cache')
        parsed[source['id']]=saved['chunks']
    for annotation in annotations:
        for locator in annotation['evidence_locators']:
            source=docs[locator['source_id']]
            source_ok=original_evidence_matches(corpus,source,locator,visual)
            hits=[]
            for i,c in enumerate(parsed[source['id']]):
                if source['format']=='pdf' and not (c['metadata']['page_start']<=locator['physical_page']<=c['metadata']['page_end']):
                    continue
                if normalize(locator['anchor']) in normalize(c['content']): hits.append(i)
            row=dict(id=annotation['id'],source_id=source['id'],physical_page=locator['physical_page'],
                     anchor=locator['anchor'],source_match=source_ok,chunk_indices=hits)
            (matches if source_ok and hits else failures).append(row)
    result=dict(evaluation_type='offline_original_page_and_actual_chunk_anchor_validation',external_model_calls=0,
                total_questions=len(cases),positive_questions=sum(c['answerable'] for c in cases),
                negative_behaviors=dict(Counter(c.get('expected_behavior') for c in cases if not c['answerable'])),
                total_locators=len(matches)+len(failures),matched=len(matches),failed=len(failures),
                matched_question_count=len({r['id'] for r in matches}-{r['id'] for r in failures}),
                corpus_manifest_sha256=frozen['corpus_manifest_sha256'],freeze_sha256=sha((evaluation/'splits.json').read_bytes()),
                parser_sha256=parser_fingerprint(),matches=matches,failures=failures,
                limitation='锚点与页码验证不等于检索召回率、真实问答准确率或独立专家复核。')
    return result


def assess_readiness(evaluation,corpus,cache,label_report):
    """Keep gold labels and parser defects separate; never relax failed anchors."""
    frozen,cases,annotations=validate_freeze(evaluation,corpus)
    if label_report['freeze_sha256']!=sha((evaluation/'splits.json').read_bytes()) or label_report['parser_sha256']!=parser_fingerprint():
        raise ValueError('Label observations belong to a different freeze or parser')
    manifest=inventory(corpus)
    failed_cases={r['id'] for r in label_report['failures']}
    records,diagnostics=[],[]
    for source in manifest['documents']:
        saved=json.loads((cache/(source['id']+'.json')).read_text(encoding='utf-8'))
        if saved['source_sha256']!=source['sha256'] or saved['parser_sha256']!=parser_fingerprint():
            raise ValueError('Stale parser observations')
        chunks=saved['chunks']
        observations=[]
        if source['format']=='pdf':
            observed=json.loads((cache.parent/'pages'/(source['id']+'.json')).read_text(encoding='utf-8'))
            if observed['source_sha256']!=source['sha256'] or observed['parser_sha256']!=parser_fingerprint():
                raise ValueError('Stale page observations')
            observations=observed['pages']
        failures=[r for r in label_report['failures'] if r['source_id']==source['id']]
        nonempty_pages=sum(bool(p['text'].strip() or p.get('table_count')) for p in observations)
        coverage=nonempty_pages/source['physical_pages'] if source['format']=='pdf' else None
        state='blocked_body_missing' if saved['record']['status']=='blocked' or (coverage is not None and coverage<0.5) else 'review_required_evidence_mismatch' if failures else 'sampled_evidence_verified'
        ids=[a['id'] for a in annotations if any(e['source_id']==source['id'] for e in a['evidence_locators'])]
        records.append(dict(id=source['id'],source_sha256=source['sha256'],status=state,chunks=len(chunks),
                            physical_pages=source['physical_pages'],extracted_nonempty_pages=nonempty_pages if observations else None,
                            page_text_coverage=coverage,positive_cases=len(ids),fully_matched_cases=sum(i not in failed_cases for i in ids),
                            unmatched_locators=len(failures),ocr_review_required=saved['record']['review_required']))
        for failure in failures:
            needle=normalize(failure['anchor'])
            original_page='\n'.join(p['text'] for p in observations if p['number']==failure['physical_page'])
            in_page=needle in normalize(original_page)
            anywhere=[i for i,c in enumerate(chunks) if needle in normalize(c['content'])]
            stage=('chunking_or_page_attribution' if in_page else
                   'ocr_text_difference_or_loss' if 'OCR必需' in source.get('features',[]) else 'native_page_extraction_order_or_loss')
            diagnostics.append({**failure,'present_in_observed_page_text':in_page,
                                'any_page_chunk_indices':anywhere,'first_observed_difference':stage,
                                'interpretation':'连续证据锚点未匹配；不单凭此项认定全部相关事实丢失或整题一定答错。'})
    return dict(edition='manufacturing-zh-v3',evaluation_type='offline_readiness_not_answer_accuracy',
                corpus_manifest_sha256=frozen['corpus_manifest_sha256'],freeze_sha256=label_report['freeze_sha256'],
                parser_sha256=parser_fingerprint(),external_model_calls=0,labels_complete=True,
                full_runtime_acceptance_ready=not failed_cases and all(r['status']=='sampled_evidence_verified' for r in records),
                status_counts=dict(Counter(r['status'] for r in records)),total_questions=len(cases),
                positive_questions=sum(c['answerable'] for c in cases),fully_matched_positive_questions=90-len(failed_cases),
                known_parser_mismatch_case_ids=sorted(failed_cases),
                negative_behavior_cases=sum(not c['answerable'] for c in cases),
                first_difference_counts=dict(Counter(r['first_observed_difference'] for r in diagnostics)),
                records=records,diagnostics=diagnostics,
                limitation='抽样证据可用不等于全文正确；低页覆盖是阻断信号，不能把空白页直接当正文丢失。未运行检索或模型问答。')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--corpus',type=Path,default=ROOT/'sample_docs/manufacturing_zh_v3')
    p.add_argument('--parse-cache',type=Path,default=ROOT/'tmp/manufacturing-zh-v3/parsed')
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--ocr',action='store_true')
    p.add_argument('--workers',type=int,choices=(1,2,3,4),default=2)
    p.add_argument('--labels',type=Path)
    p.add_argument('--readiness-from',type=Path,help='Existing strict label report; requires --labels and does not run OCR again')
    p.add_argument('--baselines',action='store_true')
    a=p.parse_args()
    if a.report.resolve().is_relative_to((a.corpus/'documents').resolve()) or a.parse_cache.resolve().is_relative_to((a.corpus/'documents').resolve()):
        p.error('Reports and caches must stay outside the upload directory')
    if a.baselines:
        pinned=json.loads((ROOT/'sample_docs/manufacturing_zh_v3.sources.json').read_text(encoding='utf-8'))['baseline_fingerprints']
        current=baseline_fingerprints()
        changed=sorted(k for k in set(pinned)|set(current) if pinned.get(k)!=current.get(k))
        result=dict(unchanged=not changed,total_files=len(pinned),changed_files=changed,
                    baseline_fingerprint_sha256=sha(json.dumps(pinned,sort_keys=True).encode()),external_model_calls=0)
        dump(a.report,result)
        print(json.dumps(result)); return int(bool(changed))
    if a.labels:
        if a.readiness_from:
            result=assess_readiness(a.labels,a.corpus,a.parse_cache,json.loads(a.readiness_from.read_text(encoding='utf-8')))
            dump(a.report,result)
            print(json.dumps({k:v for k,v in result.items() if k not in ('records','diagnostics','known_parser_mismatch_case_ids')}))
            return 0 if result['full_runtime_acceptance_ready'] else 3
        result=validate_labels(a.labels,a.corpus,a.parse_cache)
        dump(a.report,result)
        print(json.dumps({k:v for k,v in result.items() if k not in ('matches','failures')}))
        return int(bool(result['failed']))
    return parse_observed(a.corpus,a.parse_cache,a.report,ocr=a.ocr,workers=a.workers)


if __name__=='__main__': raise SystemExit(main())
