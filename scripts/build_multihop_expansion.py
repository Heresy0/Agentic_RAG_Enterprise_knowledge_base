"""Prepare six curated documents and twelve labelled cases; no model or DB calls."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CORPUS = ROOT / 'sample_docs/xinghai_multihop'
DATASET = ROOT / 'evals/xinghai_multihop'
UNIFIED = ROOT / 'output/knowledge-corpus-multihop'
EDITION = 'xinghai-multihop-6-v1'
AS_OF = '2026-10-14'
DOCUMENTS = [
    (51, '青屿项目范围与适用规则登记', '技术部', '03_技术部', 'XH-QY-SCOPE'),
    (52, '青屿验收接口规则与版本条件', '平台研发部', '04_平台研发部', 'XH-QY-ACCEPT'),
    (53, '青屿校验批次与验收签署记录', '技术部', '03_技术部', 'XH-QY-RESULT'),
    (54, '雾岑服务配置与适配器映射', '平台研发部', '04_平台研发部', 'XH-WC-CONFIG'),
    (55, '雾岑服务分级与响应约定', '平台研发部', '04_平台研发部', 'XH-WC-SLA'),
    (56, '雾岑同步与预览事件运行记录', '平台研发部', '04_平台研发部', 'XH-WC-INCIDENTS'),
]
BY_NUMBER = {row[0]: row for row in DOCUMENTS}


def source_path(number):
    n, title, _, group, _ = BY_NUMBER[number]
    return f'documents/{group}/{n}_{title}.md'


def step(number, quote, fact):
    return dict(document=Path(source_path(number)).name, source_quote=quote, extracted_fact=fact)


def case(number, kind, question, answer, facts, steps, *, entities=(), answerable=True):
    numbered = [dict(index=i, depends_on=[] if i == 1 else [i-1], **s)
                for i, s in enumerate(steps, 1)]
    names = list(dict.fromkeys(s['document'] for s in steps))
    records = [d for d in DOCUMENTS if Path(source_path(d[0])).name in names]
    groups = list(dict.fromkeys(d[2] for d in records))
    return dict(id=f'MH-{number:03d}', case_kind=kind, question=question, reference_answer=answer,
        answerable=answerable, required_facts=facts,
        expected_evidence=[dict(document=s['document'], text=s['source_quote']) for s in steps] if answerable else [],
        required_source_documents=names if answerable else [], source_groups=groups,
        expected_scope=groups[0] if len(groups)==1 else 'all_accessible', profile='全范围测试用户',
        source_file_paths=[source_path(d[0]) for d in records], category=kind, difficulty='hard',
        as_of=AS_OF, annotation_version=EDITION, manual_review_required=True,
        citation_requirement='all_required_sources' if answerable else 'no_unsupported_citation',
        hop_count=len(steps) if answerable else 0, hop_steps=numbered if answerable else [],
        intermediate_entities=list(entities),
        diagnostic_evidence=steps if not answerable else [],
        semantic_checks=['核对中间对象、配置/规则版本、时间口径和最终结论，不以关键词齐全代替语义判断。',
                         '逻辑多跳与工具调用次数分别检查；一次检索取齐证据也可以完成多跳推理。'])


def make_cases():
    rows = [
        case(1, 'two_hop', '截至2026年10月14日，青屿档案项目适用哪些全量校验与缺陷准入条件？',
            '适用AR-T2：连续2轮全量校验，两轮记录缺失数为0，附件索引比对一致，不得有未关闭的D1或D2缺陷。',
            ['2轮', '0', '附件索引', 'D1', 'D2'], [
                step(51, 'QY-PRJ-061采用验收规则AR-T2', '项目当前绑定AR-T2'),
                step(52, 'AR-T2要求连续2轮全量校验通过，两轮记录缺失数均为0，附件索引比对一致，且不得有未关闭的D1或D2缺陷', '读取AR-T2完整准入条件')], entities=['AR-T2']),
        case(2, 'two_hop', '雾岑归档同步服务当前的首响应时限是多少，采用哪个服务等级？',
            '实际配置CFG-WC-092对应SL-B2，首响应时限为8分钟。不能使用旧配置的SL-B1时限。',
            ['SL-B2', '8分钟'], [
                step(54, '实际部署配置为CFG-WC-092，配置对应服务等级SL-B2', '当前配置绑定SL-B2'),
                step(55, 'SL-B2的首响应时限为8分钟', '按SL-B2读取首响应阈值')], entities=['SL-B2']),
        case(3, 'two_hop', '雾岑10月12日上午同步故障关联哪个适配器，回滚后的配置复核归哪个小组负责？',
            '该故障关联ADP-N7，回滚后的配置复核由平台研发部连接器组负责。',
            ['ADP-N7', '连接器组'], [
                step(56, '实际使用配置CFG-WC-092和适配器ADP-N7', '事件实际使用ADP-N7'),
                step(54, 'ADP-N7由平台研发部连接器组负责，回滚后的配置复核也由该组完成', '查询适配器归口小组')], entities=['ADP-N7']),
        case(4, 'two_hop', '青屿最终验收记录保留的缺陷是否阻止签署？请说明保留依据。',
            '不阻止签署。保留缺陷QY-D-04属于D3，不阻断核心流程；客户已书面接受并明确修复期限，满足AR-T2的D3保留条件。',
            ['D3', '不阻断核心流程', '书面接受', '修复期限', '不阻止'], [
                step(53, '该包保留QY-D-04，等级为D3', '识别保留缺陷的等级'),
                step(52, 'AR-T2允许保留不阻断核心流程的D3缺陷，但客户必须书面接受，并明确修复期限', 'D3可以在满足书面接受和修复期限条件后保留'),
            ], entities=['D3']),
        case(5, 'three_hop', '截至2026年10月14日，青屿当前选用的证据包是否满足项目的全量校验和缺陷条件？',
            '满足。当前选包QY-PACK-R2适用AR-T2，已连续2轮校验且两轮缺失为0、附件索引一致、无未关闭D1或D2；保留D3已获客户书面接受并明确修复期限。',
            ['满足', '2轮', '0', '附件索引', 'D1', 'D2', '书面接受', '修复期限'], [
                step(51, '当前验收证据包为QY-PACK-R2', '确定当前选包和同段AR-T2绑定'),
                step(53, 'QY-PACK-R2分别于2026年10月9日和10月10日完成连续2轮全量校验', '取得当前选包实测结果及同节D3接受记录'),
                step(52, 'AR-T2要求连续2轮全量校验通过', '将实测结果与AR-T2逐项比对')], entities=['QY-PACK-R2', 'AR-T2']),
        case(6, 'three_hop', '雾岑10月12日上午同步故障的首响应和恢复是否都达标？采用哪些时限和计时起点？',
            '都达标。事件实际配置CFG-WC-092对应SL-B2：首响应7分50秒不超过8分钟，恢复32分钟不超过35分钟。首响应从监控首次触发算起，恢复从人工发现算起。',
            ['达标', '7分50秒', '8分钟', '32分钟', '35分钟', '监控首次触发', '人工发现'], [
                step(56, '首响应耗时7分50秒，恢复耗时32分钟', '取得事件实测和同节口径、实际配置'),
                step(54, 'CFG-WC-092，配置对应服务等级SL-B2', '把事件配置映射到SL-B2'),
                step(55, 'SL-B2的首响应时限为8分钟，恢复时限为35分钟', '按正确口径比较实际耗时与对应阈值')], entities=['CFG-WC-092', 'SL-B2']),
        case(7, 'complex', '青屿在2026年10月6日和10月10日分别需要多少轮全量校验？对未关闭D2缺陷的要求有什么变化？',
            '10月6日适用AR-T1，要求1轮；10月10日适用AR-T2，要求连续2轮。旧版未载明不得保留任何D2，新版明确不得有未关闭的D2。',
            ['1轮', '2轮', 'D2', '不得'], [
                step(51, '2026年10月1日至10月7日，QY-PRJ-061采用验收规则AR-T1', '按日期选择历史和当前规则'),
                step(52, 'AR-T1要求完成1轮全量校验', '对照AR-T1及同文档AR-T2条件变化')], entities=['AR-T1', 'AR-T2']),
        case(8, 'complex', '青屿原计划哪天最终验收，双方实际哪天签署？为什么保留缺陷仍可签署？',
            '原计划2026年10月12日，实际2026年10月13日。保留缺陷属于不阻断核心流程的D3，客户已书面接受并明确修复期限，符合AR-T2允许保留D3的条件。',
            ['10月12日', '10月13日', 'D3', '书面接受', '修复期限'], [
                step(53, '原计划最终验收日期为2026年10月12日', '区分计划、实际签署和同文档保留缺陷的接受状态'),
                step(52, '满足这两个保留条件后，D3不阻止最终签署', '解释保留缺陷不阻止签署的规则依据')]),
        case(9, 'complex', '比较雾岑10月12日上午同步故障与10月13日下午预览故障的首响应是否达标，并给出各自判断依据。',
            '同步故障达标：CFG-WC-092对应SL-B2，首响应7分50秒不超过8分钟。预览故障未达标：CFG-WC-093对应SL-C1，首响应18分钟超过15分钟。',
            ['7分50秒', '8分钟', '18分钟', '15分钟', '未达标'], [
                step(56, '首响应耗时18分钟，恢复耗时41分钟', '分别识别两个事件的配置和首响应实测'),
                step(54, 'CFG-WC-093，对应服务等级SL-C1', '将两个配置分别映射到SL-B2和SL-C1'),
                step(55, 'SL-C1的首响应时限为15分钟', '分别比较首响应阈值，不交换对象或恢复时钟')], entities=['CFG-WC-092', 'CFG-WC-093', 'SL-B2', 'SL-C1']),
        case(10, 'boundary', '青屿项目已经批准的2027年归档存储预算是多少元？',
            '资料未载明已批准金额，无法回答。', [], [
                step(51, '本登记不载明2027年归档存储预算', '在所提供的项目材料中缺少该事实')], answerable=False),
        case(11, 'boundary', '截至2026年10月14日，WC-SEC-04唯一有效的首响应时限是多少分钟？',
            '无法确定唯一时限。两份已批准、同对象同生效期的记录分别为5分钟和9分钟，未载明替代关系且尚未联合澄清，应说明冲突并拒答。', [], [
                step(55, '首响应时限为5分钟，平台研发部确认记录记载首响应时限为9分钟', '同对象同期间存在未消解的批准记录冲突')], answerable=False),
        case(12, 'boundary', '截至2026年10月14日，WC-FIX-22永久整改实际在哪天上线完成？',
            '无法确认实际完成日期。只有10月16日上线计划，10月12日的临时恢复不能证明永久整改已上线。', [], [
                step(56, '该任务只登记了计划，没有实际上线完成日期', '计划与临时恢复不能证明永久整改实际完成')], answerable=False),
    ]
    rows[3]['fact_alternatives'] = {'不阻止': ['不会阻止', '允许签署', '可签署'], '修复期限': ['修复日期']}
    rows[3]['fact_assertions'] = {'不阻止': dict(polarity='negative', contradictions=['不能签署', '不得签署', '阻止签署'])}
    rows[5]['fact_alternatives'] = {'7分50秒': ['7分钟50秒'], '监控首次触发': ['首次告警'], '人工发现': ['发现异常']}
    rows[6]['fact_alternatives'] = {'1轮': ['一轮'], '2轮': ['两轮']}
    rows[8]['fact_alternatives'] = {'7分50秒': ['7分钟50秒'], '未达标': ['不达标', '超过时限']}
    rows[8]['fact_assertions'] = {'未达标': dict(polarity='negative', contradictions=['均达标', '都达标', '预览故障达标'])}
    supporting = {
        'MH-004': {1:['客户于2026年10月11日书面接受QY-D-04，明确修复期限为2026年10月18日']},
        'MH-005': {1:['QY-PRJ-061采用验收规则AR-T2'],
                   2:['两轮记录缺失数均为0，附件索引比对一致，没有未关闭的D1或D2缺陷',
                      '客户于2026年10月11日书面接受QY-D-04，明确修复期限为2026年10月18日'],
                   3:['AR-T2允许保留不阻断核心流程的D3缺陷，但客户必须书面接受，并明确修复期限']},
        'MH-006': {1:['实际使用配置CFG-WC-092和适配器ADP-N7'],
                   3:['首响应从监控首次触发到值班人员确认接手计算',
                      '恢复从人工发现异常到业务复核确认恢复计算']},
        'MH-007': {1:['自2026年10月8日起，QY-PRJ-061采用验收规则AR-T2'],
                   2:['AR-T2要求连续2轮全量校验通过', '不得有未关闭的D1或D2缺陷',
                      '它也没有载明“不得保留任何D2缺陷”这一新增条件']},
        'MH-008': {1:['实际于2026年10月13日共同签署最终验收',
                      '客户于2026年10月11日书面接受QY-D-04，明确修复期限为2026年10月18日'],
                   2:['AR-T2允许保留不阻断核心流程的D3缺陷，但客户必须书面接受，并明确修复期限']},
        'MH-009': {1:['实际使用配置CFG-WC-092和适配器ADP-N7', '首响应耗时7分50秒，恢复耗时32分钟',
                      '实际使用配置CFG-WC-093和适配器ADP-P4'],
                   2:['CFG-WC-092，配置对应服务等级SL-B2'],
                   3:['SL-B2的首响应时限为8分钟']},
    }
    for row in rows:
        for s in row['hop_steps']:
            s['supporting_quotes'] = supporting.get(row['id'],{}).get(s['index'],[])
            row['expected_evidence'].extend(dict(document=s['document'],text=q) for q in s['supporting_quotes'])
    return rows


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n'


def artifacts(corpus=CORPUS, dataset=DATASET):
    from backend.app.documents.text_splitter import split_markdown
    from backend.app.evaluation.metrics import evidence_metrics
    corpus, dataset = Path(corpus), Path(dataset)
    docs, texts, chunks, chunk_counts = [], {}, [], {}
    for n, _, group, _, code in DOCUMENTS:
        path = corpus / source_path(n)
        body = path.read_text(encoding='utf-8')
        name = path.name; texts[name] = body
        parsed = split_markdown(path)
        chunk_counts[name] = len(parsed)
        chunks.extend(dict(document_name=name, content=c.page_content) for c in parsed)
        docs.append(dict(file=source_path(n), group=group, format='md', document_code=code,
                         characters=len(body), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    cases = make_cases()
    if Counter(c['case_kind'] for c in cases) != {'two_hop':4,'three_hop':2,'complex':3,'boundary':3}:
        raise ValueError('Unexpected case mix')
    evidence_checks = []
    for c in cases:
        for s in c['hop_steps'] + c['diagnostic_evidence']:
            if any(q not in texts[s['document']] for q in [s['source_quote'],*s.get('supporting_quotes',[])]):
                raise ValueError('Evidence not in source: '+c['id'])
        if any(entity in c['question'] for entity in c['intermediate_entities']):
            raise ValueError('Intermediate entity leaked into question: '+c['id'])
        if c['answerable']:
            metrics = evidence_metrics(c['expected_evidence'],chunks)
            if not metrics['all_evidence_present']:
                raise ValueError('Evidence missing after Markdown parsing: '+c['id'])
            evidence_checks.append(dict(id=c['id'], **metrics))
    manifest=dict(company='星海科技有限公司', fictional=True, edition=EDITION, as_of=AS_OF,
        total_documents=6, counts={'md':6}, group_counts=dict(Counter(d['group'] for d in docs)), documents=docs)
    coverage=dict(edition=EDITION, single_turn_cases=12, answerable=9, unanswerable=3,
        case_kinds=dict(Counter(c['case_kind'] for c in cases)),
        corpus_document_hashes={Path(d['file']).name:d['sha256'] for d in docs},
        document_chunk_counts=chunk_counts, total_chunks=len(chunks),
        structural_validation='passed', evidence_checks=evidence_checks,
        external_model_calls=0, database_writes=0,
        semantic_annotations_execution='manual_review_only_not_executed_by_current_scorer',
        live_answer_evaluation='not_run', limitations=['合成增量集，不是严格独立企业盲测。','逻辑证据链不等于固定工具调用次数。'])
    books={False:['# 复杂与多跳问题增量题册',''],True:['# 参考答案与证据链','']}
    for c in cases:
        for answers,book in books.items():
            book += [f"## {c['id']} · {c['case_kind']} · {c['hop_count']}跳",'',c['question'],'']
            if answers:
                book += ['参考答案：'+c['reference_answer'],'']
                for s in c['hop_steps']:
                    book += [f"- 第{s['index']}步：{s['extracted_fact']}；来源：{s['document']}；原文：{s['source_quote']}"]
                    book += [f"  补充原文：{q}" for q in s.get('supporting_quotes',[])]
                for s in c['diagnostic_evidence']:
                    book += [f"- 拒答边界：{s['extracted_fact']}；来源：{s['document']}；原文：{s['source_quote']}"]
                book += ['','人工复核：对象、版本、时钟、缺陷保留与最终结论；自动词语匹配不是语义正确性。','']
    outputs={corpus/'manifest.json':json_text(manifest), dataset/'coverage.json':json_text(coverage),
             dataset/'single_turn.jsonl':''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in cases),
             dataset/'测试题册.md':'\n'.join(books[False])+'\n', dataset/'答案与证据链.md':'\n'.join(books[True])+'\n'}
    return outputs


def build(*, check=False):
    output = artifacts()
    for path, body in output.items():
        if check:
            if not path.is_file() or path.read_bytes()!=body.encode('utf-8'):
                raise ValueError('Generated artifact out of date: '+str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(body.encode('utf-8'))
    from scripts.evaluate_answers import load_dataset
    return load_dataset(DATASET/'single_turn.jsonl')


def prepare_corpus(output=UNIFIED):
    from scripts.merge_xinghai_evaluation import combined_manifest, BASE_CORPUS, EXPANSION_CORPUS
    extra=json.loads((CORPUS/'manifest.json').read_text(encoding='utf-8'))
    base=combined_manifest()
    rows=base['documents']+extra['documents']
    output=Path(output).resolve()
    if output!=UNIFIED.resolve():
        raise ValueError('Only the dedicated ignored output directory is writable')
    roots={**{Path(d['file']).name:r for r in (BASE_CORPUS,EXPANSION_CORPUS)
               for d in json.loads((r/'manifest.json').read_text(encoding='utf-8'))['documents']},
           **{Path(d['file']).name:CORPUS for d in extra['documents']}}
    if len({Path(d['file']).name for d in rows})!=56:
        raise ValueError('Expected 56 unique source files')
    allowed={output/'manifest.json', *[(output/d['file']).resolve() for d in rows]}
    unexpected=[path for path in output.rglob('*') if path.is_file() and path.resolve() not in allowed]
    if unexpected:
        raise ValueError('Unexpected files in managed corpus; refusing to include review notes or answers')
    for d in rows:
        root=roots[Path(d['file']).name].resolve();source=(root/d['file']).resolve();target=(output/d['file']).resolve()
        if not source.is_relative_to(root/'documents') or not target.is_relative_to(output/'documents'):
            raise ValueError('Document path outside managed roots')
        if hashlib.sha256(source.read_bytes()).hexdigest()!=d['sha256']:
            raise ValueError('Source hash changed')
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
    manifest=dict(company=base['company'],fictional=True,edition='xinghai-with-multihop-56-v1',total_documents=56,
        counts=dict(Counter(d['format'] for d in rows)),group_counts=dict(Counter(d['group'] for d in rows)),documents=rows)
    (output/'manifest.json').write_bytes(json_text(manifest).encode('utf-8'))
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--prepare-corpus',action='store_true')
    args=parser.parse_args()
    build(check=args.check)
    if args.prepare_corpus:
        prepare_corpus()
    print(json.dumps(dict(documents=6,cases=12,answerable=9,unanswerable=3,model_calls=0,database_writes=0)))
