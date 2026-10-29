"""Freeze 40 development and 80 test cases from locked Chinese originals."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from lxml import html
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.build_manufacturing_public import dump,sha
from scripts.build_manufacturing_zh import guard_output
from scripts.manufacturing_zh_questions import POSITIVE,NEGATIVE
from scripts.validate_manufacturing_public import inventory
from scripts.evaluate_answers import normalize


def original_evidence_matches(corpus,source,locator,visual):
    page=locator['physical_page']
    anchor=normalize(locator['anchor'])
    if len(anchor)<2: return False
    if source['format']=='md':
        if page is not None: return False
        tree=html.fromstring((corpus/'originals'/(source['id']+'.html')).read_bytes().decode('utf-8-sig'))
        nodes=tree.xpath(source['content_xpath'])
        return len(nodes)==1 and anchor in normalize(' '.join(nodes[0].itertext()))
    if not isinstance(page,int) or not 1<=page<=source['physical_pages']: return False
    if 'OCR必需' not in source.get('features',[]):
        return anchor in normalize(PdfReader(corpus/source['file']).pages[page-1].extract_text())
    reviewed=[r for r in visual['pages'] if r['source_id']==source['id'] and r['physical_page']==page]
    return bool(len(reviewed)==1 and reviewed[0]['source_sha256']==source['sha256']
                and reviewed[0]['verification']=='complete_original_page_visually_inspected'
                and locator['anchor'] in reviewed[0]['verified_anchors']
                and len(reviewed[0]['render_sha256'])==64)


def build_cases(corpus,visual):
    manifest=inventory(corpus)
    by_id={s['id']:s for s in manifest['documents']}
    if len(POSITIVE)!=90 or len(NEGATIVE)!=30 or set(Counter(s['source_id'] for s in POSITIVE).values())!={5}:
        raise ValueError('Expected five substantive cases per source and 30 explicit negative behaviors')
    splits={'development':[],'test':[]}
    annotations=[]
    for index,spec in enumerate(sorted(POSITIVE,key=lambda s:s['source_id']),1):
        source=by_id[spec['source_id']]
        if any(normalize(f) not in normalize(spec['reference_answer']) for f in spec['required_facts']):
            raise ValueError('Required fact absent from reference answer: '+spec['concept_id'])
        identity=f'MFGZH-Q{index:03}'
        locators=[]
        for evidence in spec['evidence']:
            locator={**evidence,'source_id':source['id'],'source_sha256':source['sha256'],
                     'document':source['file'],'source_url':source['source_url'],
                     'verification':'visual_original_scan' if 'OCR必需' in source.get('features',[]) else 'original_text'}
            if not original_evidence_matches(corpus,source,locator,visual):
                raise ValueError('Original evidence not verified: '+spec['concept_id']+' / '+evidence['anchor'])
            locators.append(locator)
        title=source['title'].replace('_',' ')
        case=dict(id=identity,question=f'按《{title}》所收录原文，'+spec['question'],
                  reference_answer=spec['reference_answer'],answerable=True,required_facts=spec['required_facts'],
                  expected_evidence=[dict(document=Path(source['file']).name,text=e['anchor']) for e in spec['evidence']],
                  source_groups=['公共参考'],expected_scope='公共参考',source_file_paths=[source['file']],
                  required_source_documents=[Path(source['file']).name],citation_requirement='all_supporting_sources',
                  category='生产质量与追溯' if int(source['id'][-2:])<5 else '设备与量具校准',
                  difficulty='medium',annotation_version='manufacturing-zh-v3',profile='中文公共参考测试用户',
                  manual_review_required=True,semantic_checks=[spec['reference_answer']],optional_facts=[],forbidden_claims=[])
        splits[source['split']].append(case)
        annotations.append(dict(id=identity,split=source['split'],concept_id=spec['concept_id'],baseline=False,
                                source_family=source['source_family'],evidence_locators=locators))
    for index,(concept,split,behavior,question,answer,missing) in enumerate(NEGATIVE,1):
        identity=f'MFGZH-N{index:02}'
        case=dict(id=identity,question=question,reference_answer=answer,answerable=False,
                  required_facts=[],expected_evidence=[],source_groups=['公共参考'],expected_scope='公共参考',
                  source_file_paths=[],required_source_documents=[],citation_requirement='none',
                  category='证据不足' if behavior=='refuse' else '条件缺失',difficulty='medium',
                  annotation_version='manufacturing-zh-v3',profile='中文公共参考测试用户',
                  expected_behavior=behavior,missing_information=missing,manual_review_required=True,
                  semantic_checks=[answer],optional_facts=[],forbidden_claims=['未经证据确认结果或声称已执行操作'])
        splits[split].append(case)
        annotations.append(dict(id=identity,split=split,concept_id=concept,baseline=False,evidence_locators=[]))
    if {k:len(v) for k,v in splits.items()}!={'development':40,'test':80}:
        raise ValueError('Wrong split sizes')
    return splits,annotations


def build(corpus,output,visual_path):
    guard_output(output)
    if output.resolve().is_relative_to(corpus.resolve()):
        raise ValueError('Gold labels must stay outside the corpus and upload directory')
    if output.exists() and any(output.iterdir()): raise ValueError('Existing frozen evaluations are never overwritten')
    visual=json.loads(visual_path.read_text(encoding='utf-8'))
    splits,annotations=build_cases(corpus,visual)
    for split,rows in splits.items():
        path=output/split/'single_turn.jsonl'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(('\n'.join(json.dumps(r,ensure_ascii=False) for r in rows)+'\n').encode())
    dump(output/'annotations.json',annotations)
    dump(output/'visual-review.json',visual)
    freeze=dict(edition='manufacturing-zh-v3',status='frozen_before_first_chinese_model_run',
                corpus_manifest_sha256=sha((corpus/'manifest.json').read_bytes()),
                source_lock_sha256=sha((ROOT/'sample_docs/manufacturing_zh_v3.sources.json').read_bytes()),
                annotations_sha256=sha((output/'annotations.json').read_bytes()),
                visual_review_sha256=sha((output/'visual-review.json').read_bytes()),
                counts={s:len(rows) for s,rows in splits.items()},
                dataset_sha256={s:sha((output/s/'single_turn.jsonl').read_bytes()) for s in splits},
                positive_questions=90,negative_behaviors=dict(Counter(r[2] for r in NEGATIVE)),
                roles=dict(development='用于后续调试，40题；未执行模型问答。',test='80题，按来源族隔离；未用于模型驱动调参，标签可审计，非秘密或独立专家基准。'),
                provenance='同一助手依据原文逐项编写和检查，不是独立专家认证，也不是实测正确率。',
                readiness='另见离线报告；结构非空不等于所有正文和证据都已成功解析。',external_model_calls=0)
    dump(output/'splits.json',freeze)
    return freeze


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--corpus',type=Path,default=ROOT/'sample_docs/manufacturing_zh_v3')
    p.add_argument('--output',type=Path,default=ROOT/'evals/manufacturing_zh_v3')
    p.add_argument('--visual-review',type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(build(a.corpus,a.output,a.visual_review),ensure_ascii=False))


if __name__=='__main__': main()
