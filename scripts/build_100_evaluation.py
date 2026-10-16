"""Build a versioned 100-case pack and 12 synthetic sources; offline by default."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.corpus_100_data import AS_OF, DOCUMENTS, EDITION, GROUPS, make_cases, render_document, source_path
from scripts.build_knowledge_expansion import write_artifacts

CORPUS=ROOT/'sample_docs/xinghai_nanqi'
DATASET=ROOT/'evals/xinghai_v4'
DELTA=ROOT/'evals/xinghai_nanqi'
LEGACY=ROOT/'evals/xinghai_v3'
UNIFIED=ROOT/'output/knowledge-corpus-100'
SOURCE_ROOTS=[ROOT/'sample_docs/xinghai_base',ROOT/'sample_docs/xinghai_expansion',ROOT/'sample_docs/xinghai_multihop']

def dumps(value):return json.dumps(value,ensure_ascii=False,indent=2)+'\n'
def jsonl(rows):return ''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def load(path):return json.loads(path.read_text(encoding='utf-8-sig'))

CORPUS_GUIDE='''# 南栖项目知识库扩容包

公司、项目、人员、事件、规范与数值均为合成设定，用于本项目功能回归；不是实际企业数据或独立盲测。
本包新增12份Markdown业务文档，四个现有知识库各3份，编号57至68，仅适用于NQ-2026-09。
原80题、50份主语料与6份多跳增量语料保持原样；南栖专用规则不替换其他项目规则。

只将documents中对应部门的业务正文上传至同名知识库。README、manifest、入库清单、题目、答案和证据标注不入库。
公共资料仅保留范围及路由，不公开HR任务明细和平台运行时限。跨部门引用不改变用户授权。

事实源为scripts/corpus_100_data.py；生成器不请求模型、不连接数据库。声明集中在此说明和manifest，业务正文不混入测试答案或系统指令。

```powershell
python scripts/build_100_evaluation.py --prepare-corpus
python scripts/build_100_evaluation.py --check
```

新增20题位于evals/xinghai_nanqi；合并100题位于evals/xinghai_v4。
真实上传通过现有OIDC与editor权限保护的API，索引会使用当前配置的Embedding服务并产生费用。
本地生成和解析通过不能证明已经入库、在线召回或模型回答正确；入库结果另存本地运行收据。
'''


def build_artifacts(corpus=CORPUS,dataset=DATASET,delta=DELTA):
    corpus,dataset,delta=map(Path,(corpus,dataset,delta))
    texts={source_path(d[0]):render_document(d) for d in DOCUMENTS}
    by_name={Path(name).name:body for name,body in texts.items()}
    cases=make_cases()
    if len(texts)!=12 or len(cases)!=20 or sum(c['answerable'] for c in cases)!=16:
        raise ValueError('Expected twelve documents and sixteen positive/four negative new cases')
    if Counter(d[2] for d in DOCUMENTS)!=dict.fromkeys(GROUPS,3):
        raise ValueError('Expected three new documents per group')
    from backend.app.evaluation.metrics import score_answer
    covered=set()
    for c in cases:
        for ev in c['expected_evidence']+c['diagnostic_evidence']:
            if ev['text'] not in by_name[ev['document']]:raise ValueError('Missing source anchor: '+c['id']+' '+ev['text'])
        covered.update(ev['document'] for ev in c['expected_evidence'])
        response=dict(answerable=c['answerable'],answer=c['reference_answer'],citations=[dict(document_name=n) for n in c['required_source_documents']])
        if not score_answer(c,response)['automatic_proxy_pass']:raise ValueError('Reference answer fails scorer: '+c['id'])
    if covered!=set(by_name):raise ValueError('All new documents need positive evidence coverage')
    entries=[dict(file=source_path(n),group=g,format='md',document_code=code,business_version=v,effective_from=start,
                  effective_to=end,characters=len(texts[source_path(n)]),sha256=sha(texts[source_path(n)].encode('utf-8')))
             for n,title,g,code,v,start,end,_ in DOCUMENTS]
    manifest=dict(company='星海科技有限公司',fictional=True,edition=EDITION,as_of=AS_OF,total_documents=12,
                  counts={'md':12},group_counts=dict(Counter(r['group'] for r in entries)),documents=entries)
    legacy_raw=(LEGACY/'single_turn.jsonl').read_bytes()
    legacy=[json.loads(line) for line in legacy_raw.decode('utf-8-sig').splitlines() if line.strip()]
    if len(legacy)!=80 or not legacy_raw.endswith(b'\n'):raise ValueError('Expected intact newline-terminated legacy80 pack')
    rows=legacy+cases
    if len({r['id'] for r in rows})!=100:raise ValueError('Duplicate case IDs')
    # Copy the original serialization as well as retaining every legacy field.
    combined_text=legacy_raw.decode('utf-8')+jsonl(cases)
    coverage=dict(edition='xinghai-100-v1',as_of=AS_OF,as_of_by_suite={'XH':'2026-10-04','CC':'2026-10-13','NQ':AS_OF},single_turn_cases=100,answerable=87,unanswerable=13,
        legacy_cases=80,new_cases=20,new_documents=12,legacy_dataset_sha256=sha(legacy_raw),
        inherited_conversations=8,inherited_conversation_turns=24,inherited_security_cases=12,
        categories=dict(Counter(r['category'] for r in rows)),
        evidence_source_documents=len({ev['document'] for r in rows for ev in r['expected_evidence']}),
        generated_model_calls=0,database_writes=0,live_answer_evaluation='not_run',
        limitation='Synthetic regression labels and static checks do not establish online quality or independent semantic accuracy.')
    result={corpus/name:body for name,body in texts.items()}
    result.update({corpus/'manifest.json':dumps(manifest),corpus/'README.md':CORPUS_GUIDE,
                   dataset/'single_turn.jsonl':combined_text,dataset/'coverage.json':dumps(coverage),
                   delta/'single_turn.jsonl':jsonl(cases),delta/'coverage.json':dumps(dict(coverage,edition=EDITION,as_of_by_suite={'NQ':AS_OF},categories=dict(Counter(c['category'] for c in cases)),single_turn_cases=20,answerable=16,unanswerable=4,legacy_cases=0,evidence_source_documents=12,inherited_conversations=0,inherited_conversation_turns=0,inherited_security_cases=0))})
    output=io.StringIO(newline='');writer=csv.DictWriter(output,fieldnames=['file','group','sha256'],lineterminator='\n');writer.writeheader()
    writer.writerows({k:r[k] for k in writer.fieldnames} for r in entries)
    result[corpus/'入库清单.csv']=output.getvalue()
    for target,subset in [(dataset,rows),(delta,cases)]:
        for group in GROUPS:
            result[target/'by_scope'/f'{group}.jsonl']=jsonl([c for c in subset if c['source_groups']==[group]])
        result[target/'cross_scope.jsonl']=jsonl([c for c in subset if len(c['source_groups'])>1])
        result[target/'unanswerable.jsonl']=jsonl([c for c in subset if not c['answerable']])
        books={False:['# 业务问答题册',''],True:['# 参考答案与证据标注','']}
        for c in subset:
            for answers,book in books.items():
                book += [f"## {c['id']} · {c['category']}",'',c['question'],'',f"范围：{c['expected_scope']}；业务日期：{c['as_of']}。",'']
                if answers:
                    book += ['参考答案：'+c['reference_answer'],'','必答事实：'+'；'.join(c['required_facts']),'']
                    book += ['- '+ev['document']+'：'+ev['text'] for ev in c['expected_evidence']]
                    book += ['- 拒答边界 '+ev['document']+'：'+ev['text'] for ev in c.get('diagnostic_evidence',[])]
                    book += ['','语义检查：'+'；'.join(c.get('semantic_checks',[])),'']
        result[target/'测试题册.md']='\n'.join(books[False]).rstrip()+'\n'
        result[target/'答案与评分.md']='\n'.join(books[True]).rstrip()+'\n'
    for filename in ('conversations.json','permissions.json'):
        result[dataset/filename]=(LEGACY/filename).read_bytes().decode('utf-8')
    result[dataset/'README.md']='''# 星海100题评估集 v4

主单轮共100题：原80题逐字保留，增加NQ-001至NQ-020；87道可回答、13道资料不足。
原XH题业务日期为2026-10-04、CC题为2026-10-13，新增NQ题为2026-10-16；明确日期仍以各题为准。
另继承原8组24轮多轮与12项权限用例，共120个案例、136个检查步骤；本次未新增多轮或权限状态案例。

新增12份南栖文档分布在现有四库，每库3份。统一解析语料包含原50份、原6份多跳增量和本12份，共68份；100题的正例证据覆盖其中62份，原6份多跳资料仍作为检索背景。数据库可能另有未列入此清单的旧文档。
该集合属于同源合成功能回归，不是从未参与开发的独立企业盲测。新增规则只约束南栖项目，不填补原80题资料不足项所缺事实。

每道单轮题使用新会话；多轮只保留组内历史。题目、参考答案、manifest和证据标注均不上传知识库。
旧80题保留于evals/xinghai_v3，旧报告不改写；100题与80题不得直接比较总体通过率。
自动评分按既有事实短语、引用及标注证据检查；semantic_checks仍需人工审核。关键数字必须绑定正确角色、日期和对象，关键词齐全不等于语义正确。

人工口径延续原题集：可回答题每题5分（事实2、必要限定1、引用支持2），资料不足题每题5分（不编造3、信息边界1、引用使用1）。单轮共500分，多轮120分；权限12项单独判定，任何泄漏为安全失败，未测试不得记为通过。

```powershell
python scripts/build_100_evaluation.py --prepare-corpus
python scripts/build_100_evaluation.py --check
python scripts/evaluate_system.py --mode offline --layers parsing --ocr --strict
# 真实评估须配置有效JWT，使用已有授权和模型；不会自动创建账号或扩大权限。
python scripts/evaluate_system.py --mode live --config evals/system_config.local.json --layers retrieval,rerank,context,answer --allow-model-calls --save-traces
```

默认统一入口使用本目录及output/knowledge-corpus-100。仅测新增题可指定--dataset-dir evals/xinghai_nanqi，并显式选择retrieval,rerank,context,answer；增量目录没有多轮和权限文件，不能使用默认all。
生成和离线校验没有执行模型质量评估。入库收据与后续真实成绩另行记录，不以coverage.json冒充召回或准确率。
'''
    result[delta/'README.md']='''# 南栖20题增量

16道可回答、4道资料不足；配套sample_docs/xinghai_nanqi的12份合成文档。
本目录只保存新增题，完整100题在evals/xinghai_v4。问题和答案不入库，单轮题互不保留上下文。
覆盖历史/现行标准、完整日与半日、累计量、订阅对象绑定、累积重试时钟、跨库证据链、计划/实际及拒答边界。

```powershell
python scripts/evaluate_system.py --mode live --config evals/system_config.local.json --dataset-dir evals/xinghai_nanqi --corpus output/knowledge-corpus-100 --layers retrieval,rerank,context,answer --allow-model-calls --save-traces
```

只在已授权的环境执行真实调用。没有多轮/权限状态用例，不使用默认all。语义检查由人工执行，自动代理不代表独立语义准确率。
'''
    return result


def validate_parsed(corpus=CORPUS):
    from backend.app.documents.text_splitter import split_markdown
    from backend.app.evaluation.metrics import evidence_metrics
    corpus=Path(corpus)
    manifest=load(corpus/'manifest.json')
    rows=[dict(document_name=Path(d['file']).name,content=chunk.page_content)
          for d in manifest['documents'] for chunk in split_markdown(corpus/d['file'])]
    for c in make_cases():
        anchors=c['expected_evidence'] or c['diagnostic_evidence']
        if not evidence_metrics(anchors,rows)['all_evidence_present']:raise ValueError('Evidence lost by parser: '+c['id'])
    expected={corpus/d['file'] for d in manifest['documents']}
    if {p for p in (corpus/'documents').rglob('*') if p.is_file()}!=expected:raise ValueError('Unexpected files in ingestible documents')
    return len(rows)


def prepare_corpus(output=UNIFIED):
    output=Path(output).resolve()
    roots=[*SOURCE_ROOTS,CORPUS]
    if any(output.is_relative_to(r.resolve()) or r.resolve().is_relative_to(output) for r in roots):raise ValueError('Output overlaps source corpus')
    copies=[];entries=[]
    for root in roots:
        root=root.resolve()
        for row in load(root/'manifest.json')['documents']:
            source=(root/row['file']).resolve();target=(output/row['file']).resolve()
            if not source.is_relative_to(root/'documents') or not target.is_relative_to(output/'documents'):raise ValueError('Source path outside documents')
            if sha(source.read_bytes())!=row['sha256']:raise ValueError('Source hash changed: '+row['file'])
            copies.append((source,target));entries.append(row)
    if len(entries)!=68 or len({Path(r['file']).name for r in entries})!=68:raise ValueError('Expected 68 unique source files')
    expected={output/'manifest.json',*(target for _,target in copies)}
    if any(p.resolve() not in expected for p in output.rglob('*') if p.is_file()):raise ValueError('Unexpected files in combined corpus')
    manifest=dict(company='星海科技有限公司',fictional=True,edition='xinghai-with-nanqi-68-v1',total_documents=68,
        counts=dict(Counter(r['format'] for r in entries)),group_counts=dict(Counter(r['group'] for r in entries)),documents=entries)
    for source,target in copies:
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists() or target.read_bytes()!=source.read_bytes():shutil.copyfile(source,target)
    (output/'manifest.json').write_bytes(dumps(manifest).encode('utf-8'))
    return manifest


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group();mode.add_argument('--check',action='store_true');mode.add_argument('--overwrite',action='store_true')
    parser.add_argument('--prepare-corpus',action='store_true');args=parser.parse_args(argv)
    if args.check and args.prepare_corpus:parser.error('--check is read-only; prepare corpus separately')
    artifacts=build_artifacts()
    changed=write_artifacts(artifacts,check=args.check,overwrite=args.overwrite)
    chunks=validate_parsed()
    if args.prepare_corpus:prepare_corpus()
    print(json.dumps(dict(status='verified' if args.check else 'generated',changed_files=changed,documents=12,
        delta_cases=20,total_cases=100,answerable=87,unanswerable=13,new_source_chunks=chunks,model_calls=0,database_writes=0),ensure_ascii=False))

if __name__=='__main__':main()
