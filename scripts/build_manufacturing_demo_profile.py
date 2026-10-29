"""Deterministic interview/regression selection; originals and their annotations remain immutable."""
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
PROFILE=ROOT/'evals/manufacturing_zh_lite_v1'
OCR_PASSED=('MFGZH-Q056','MFGZH-Q058','MFGZH-Q067','MFGZH-Q074','MFGZH-Q076')
OCR_FAILURES={'MFGZH-Q068':'关键温度和变化率识别损坏',
              'MFGZH-Q071':'库内有证据但最终上下文未覆盖',
              'MFGZH-Q077':'原文标识字段未完整解析入库'}

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8'))

def build():
    corpus=ROOT/'sample_docs/manufacturing_zh_v3';base=ROOT/'evals/manufacturing_zh_v3'
    manifest=read(corpus/'manifest.json')
    excluded={'MFG-ZH11','MFG-ZH13'}
    docs=[row for row in manifest['documents'] if row['id']not in excluded]
    selected_ocr=set(OCR_PASSED)|set(OCR_FAILURES);splits={}
    profile=dict(version='manufacturing-zh-lite-v1',purpose='面试演示及已知结果的精简回归，非独立留出或盲测',
        baseline_ref='caf26ea',source_corpus='sample_docs/manufacturing_zh_v3',source_evaluation='evals/manufacturing_zh_v3',
        corpus_manifest_sha256=sha(corpus/'manifest.json'),
        documents=[dict(id=d['id'],file=d['file'],sha256=d['sha256'],group=d['group'])for d in docs],
        ocr_passed=list(OCR_PASSED),ocr_known_failures=OCR_FAILURES,splits=splits)
    PROFILE.mkdir(parents=True,exist_ok=True)
    for target,original in (('development','development'),('regression','test')):
        source=base/original/'single_turn.jsonl'
        rows=[json.loads(line) for line in source.read_text(encoding='utf-8').splitlines()if line]
        ocr_files={Path(d['file']).name for d in manifest['documents']if 'OCR必需'in d['features']}
        kept=[r for r in rows if not (set(r['required_source_documents'])&ocr_files) or r['id']in selected_ocr]
        path=PROFILE/target/'single_turn.jsonl';path.parent.mkdir(exist_ok=True)
        # Same JSON objects, original question/reference/requirements/scope; reviewed scores live separately.
        raw=''.join(json.dumps(row,ensure_ascii=False)+'\n'for row in kept)
        if path.exists() and path.read_text(encoding='utf-8')!=raw:raise ValueError('已有精简题集不同，须新增版本')
        path.write_text(raw,encoding='utf-8')
        splits[target]=dict(source_split=original,source_sha256=sha(source),sha256=sha(path),
            ids=[r['id']for r in kept],count=len(kept))
    target=PROFILE/'profile.json';raw=json.dumps(profile,ensure_ascii=False,indent=2)+'\n'
    if target.exists() and target.read_text(encoding='utf-8')!=raw:raise ValueError('已有配置不同，须新增版本')
    target.write_text(raw,encoding='utf-8');return profile

def load_profile(path):
    path=Path(path).resolve();profile=read(path)
    if profile['version']!='manufacturing-zh-lite-v1':raise ValueError('未知演示配置版本')
    if profile['source_corpus']!='sample_docs/manufacturing_zh_v3' or profile['source_evaluation']!='evals/manufacturing_zh_v3':raise ValueError('原始资料包不符')
    if set(profile['splits'])!={'development','regression'}:raise ValueError('分组不符')
    corpus=ROOT/profile['source_corpus'];base=ROOT/profile['source_evaluation']
    if sha(corpus/'manifest.json')!=profile['corpus_manifest_sha256']:raise ValueError('原语料清单变化')
    original=read(corpus/'manifest.json');expected={d['id']:d for d in original['documents']}
    ids=[d['id']for d in profile['documents']]
    if len(ids)!=len(set(ids)) or set(ids)!=(set(expected)-{'MFG-ZH11','MFG-ZH13'}):raise ValueError('演示资料范围不符')
    for d in profile['documents']:
        if any(d[k]!=expected[d['id']][k]for k in ('file','sha256','group')) or sha(corpus/d['file'])!=d['sha256']:
            raise ValueError('原件或权限分组变化')
    selected=set(OCR_PASSED)|set(OCR_FAILURES)
    ocr_files={Path(d['file']).name for d in original['documents']if 'OCR必需'in d['features']}
    for split,definition in profile['splits'].items():
        if definition['source_split']!=('development'if split=='development'else 'test'):raise ValueError('原分组不符')
        source=base/definition['source_split']/'single_turn.jsonl';target=path.parent/split/'single_turn.jsonl'
        if sha(source)!=definition['source_sha256'] or sha(target)!=definition['sha256']:raise ValueError('题集指纹变化')
        rows=[json.loads(line)for line in source.read_text(encoding='utf-8').splitlines()if line]
        kept=[r for r in rows if not set(r['required_source_documents'])&ocr_files or r['id']in selected]
        actual=[json.loads(line)for line in target.read_text(encoding='utf-8').splitlines()if line]
        if actual!=kept or definition['ids']!=[r['id']for r in kept] or definition['count']!=len(kept):raise ValueError('题目、答案、范围或选择被更改')
        available={Path(d['file']).name for d in profile['documents']}
        if any(not set(c['required_source_documents'])<=available for c in kept):raise ValueError('保留题目依赖已移除原件')
    return profile

if __name__=='__main__':
    result=build();load_profile(PROFILE/'profile.json')
    print(json.dumps(dict(documents=len(result['documents']),splits={k:r['count']for k,r in result['splits'].items()})))
