"""Check v2 question/source lineage locally, with no network or model execution."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import json
import re
import sys

from scripts.build_manufacturing_gold_100_v2 import (ROOT, BASE, BASE_CORPUS, DATA, CORPUS,
    COUNTS, REVISED_SOURCES, canonical, digest, read, rows, render_review)
from scripts.check_manufacturing_gold_100 import require, verify_sources, value_at

SCOPE_CASES = {8, 18, 19, 44, 45, 46, 61, 63, 79, 80, 81}


def validate(cases, sources, reviews, manifest, lineage):
    require(len(cases) == 100 and Counter(c['category'] for c in cases) == COUNTS, '题量或分类错误')
    require(len(reviews) == 100 and len({r['case_id'] for r in reviews}) == 100, '缺少逐题复核')
    require(manifest['dataset'] == 'manufacturing_gold_100_v2' and manifest['assistant_reviewed_cases'] == 100, '候选身份错误')
    require(manifest['status'] == 'candidate_assistant_reviewed_non_independent', '非独立状态错误')
    require(all(manifest[k] == 0 for k in ('human_approved_cases', 'professional_factory_approved_cases', 'platform_approved_cases', 'model_calls', 'executed_cases')), '不能伪造人工批准或成绩')
    require(not any(manifest[k] for k in ('executable', 'live_verified', 'old_results_combined')), '不可执行、实时或拼分状态错误')
    require(manifest['test_modes'] == dict(Counter(c['test_mode'] for c in cases)) ==
            dict(legacy_manufacturing_candidate=40, offline_document_candidate=54, integration_protocol_candidate=6), '模式变化')
    base = rows(BASE / 'cases.jsonl'); old_sources = read(BASE / 'sources.json')['sources']
    require(set(sources) == set(old_sources), '来源登记变化')
    require(all(sources[k] == v for k, v in old_sources.items() if k not in REVISED_SOURCES), '无关来源或历史投影被改写')
    require(set(lineage['scope_revised_cases']) == {f'MFG-GOLD-{i:03}' for i in SCOPE_CASES}, '范围修订清单错误')
    require(lineage['v1_scores_recomputed'] is False and lineage['independent_holdout'] is False, '不能改算旧分或伪装盲测')
    review_map = {r['case_id']: r for r in reviews}; actual_changes = {}
    for i, c in enumerate(cases, 1):
        require(c['id'] == c['review_id'] == f'MFG-GOLD-{i:03}', '编号或复核关联错误')
        before = base[i - 1]; x = c['expected']; r = review_map[c['id']]
        require(c['input'] == before['input'] and c['provenance'] == before['provenance'] and
                c['category'] == before['category'] and c['test_mode'] == before['test_mode'], '对象、分组或协议不能随标注修订改变')
        require(c['provenance']['independent_holdout'] is False and c['provenance']['leakage_groups'], '同工单/资料分组丢失')
        require(r['reviewer_type'] == 'assistant' and r['human_reviewer'] is None and r['independent'] is False
                and r['professional_factory_review'] is False and r['status'] == 'assistant_reviewed_non_independent'
                and r['decision'] == 'accepted_as_candidate' and r['reviewed_at'] and r['rationale'], '复核身份或依据错误')
        require(all(x[k] == before['expected'][k] for k in ('behavior', 'answerable', 'http_status', 'forbidden_claims', 'grading')), '禁止结论或行为标准被放松')
        es = {e['id']: e for e in c['evidence']}
        require(len(es) == len(c['evidence']) and r['evidence_ids'] == list(es), '证据关联错误')
        for bucket in ('required_facts', 'optional_facts'):
            facts = x[bucket]
            require(len({f['id'] for f in facts}) == len(facts), '必答或可选编号重复')
            for f in facts:
                require(f['text'] and f['evidence_ids'] and set(f['evidence_ids']) <= set(es), '要点断开来源')
        require(x['required_facts'] and x['reference_answer'], '标准答案为空')
        for e in c['evidence']:
            source = sources[e['source_id']]
            if e['kind'] == 'text':
                require(e['quote'] == next(u['text'] for u in source['units'] if u['id'] == e['unit_id']), '摘录不符')
            elif e['kind'] == 'field':
                require(source['kind'] in ('archived_business_snapshot', 'archived_mapping_snapshot'), '合成文档不能冒充实时字段')
                value = value_at(source['data'], e['field_path'])
                require(value == e['value'] and type(value) is type(e['value']), '字段值或类型变化')
            else:
                require(e['kind'] == 'full_text' and source.get('unit_coverage') != 'selected_sections_not_full_document'
                        and e['unit_ids'] == [u['id'] for u in source['units']], '全文范围错误')
        expected = deepcopy(before)
        for e in expected['evidence']:
            if e['kind'] == 'text' and e['source_id'] in REVISED_SOURCES:
                e['quote'] = next(u['text'] for u in sources[e['source_id']]['units'] if u['id'] == e['unit_id'])
        if i not in SCOPE_CASES:
            require(c == expected, '无关题目被改写')
        if c != before:
            actual_changes[c['id']] = (digest(canonical(before)), digest(canonical(c)))
    require(len({c['question'] for c in cases}) == 100, '问题重复')
    require(set(actual_changes) == {r['case_id'] for r in lineage['changes']}, '修订漏登记')
    for change in lineage['changes']:
        require(actual_changes[change['case_id']] == (change['old_case_sha256'], change['new_case_sha256']) and change['reason'], '修订指纹不符')
    for number, forbidden in {8: ('原始读数',), 18: ('精度',), 19: ('精度',),
                              45: ('子级', '未登记'), 63: ('子级', '未登记'), 81: ('子级', '未登记'),
                              46: ('UTC', '时区'), 44: ('分别保存', '分别核对'), 80: ('分别保存', '分别核对')}.items():
        text = '；'.join(f['text'] for f in cases[number - 1]['expected']['required_facts'])
        require(not any(term in text for term in forbidden), '未问内容恢复成必答')
    for number in (61, 79):
        require(len(cases[number - 1]['expected']['required_facts']) == 8 and
                cases[number - 1]['question'] != base[number - 1]['question'], '完整标准未明确到问句或未拆开要点')
    return dict(scope_revised=len(SCOPE_CASES), changed_records=len(actual_changes),
                required_facts=sum(len(c['expected']['required_facts']) for c in cases),
                optional_facts=sum(len(c['expected']['optional_facts']) for c in cases))


def check_corpus():
    manifest = read(CORPUS / 'manifest.json')
    for path, checksum in manifest['files_sha256'].items():
        require(digest((CORPUS / path).read_bytes()) == checksum, '语料冻结文件变化')
    inventory = read(CORPUS / 'sources.json')['documents']
    parent = {r['id']: r for r in read(BASE_CORPUS / 'sources.json')['documents']}
    require(len(inventory) == 55 and set(parent) == {r['id'] for r in inventory}, '逻辑资料清单变化')
    timestamps = []
    for record in inventory:
        path = ROOT / record['repository_path']
        require(digest(path.read_bytes()) == record['sha256'], '资料原件变化')
        if record['id'] not in REVISED_SOURCES:
            require(record == parent[record['id']], '无关资料登记被改写')
            continue
        require(record['supersedes']['sha256'] == parent[record['id']]['sha256'], '原件继承错误')
        text = path.read_text(encoding='utf-8')
        require(not re.search(r'\d{4}-\d{2}-\d{4}:\d{2}:\d{2}', text), '日期与时刻仍粘连')
        values = re.findall(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00', text)
        require(len(values) == 3 and all(datetime.fromisoformat(t).utcoffset().total_seconds() == 28800 for t in values), '标准时间不可解析')
        old = (ROOT / parent[record['id']]['repository_path']).read_text(encoding='utf-8')
        require(text.replace('T08:', '08:').replace('T09:', '09:') == old, '格式修订改变了业务正文')
        timestamps.extend(values)
    return dict(logical_documents=55, revised_originals=3, valid_full_timestamps=len(timestamps))


def damage_probes(cases, sources, reviews, manifest, lineage):
    mutations = {
        '伪造人工批准': lambda c,s,r,m: m.update(human_approved_cases=1),
        '伪造盲测': lambda c,s,r,m: c[0]['provenance'].update(independent_holdout=True),
        '字段类型改写': lambda c,s,r,m: c[0]['evidence'][0].update(value=1),
        '断开证据': lambda c,s,r,m: c[7]['expected']['required_facts'][0].update(evidence_ids=[]),
        '错误原文': lambda c,s,r,m: c[48]['evidence'][0].update(quote='伪造开工'),
        '恢复未问精度': lambda c,s,r,m: c[18]['expected']['required_facts'].append(dict(id='F99',text='精度必须输出',evidence_ids=['E01'])),
        '移除禁止推论': lambda c,s,r,m: c[59]['expected'].update(forbidden_claims=[]),
        '改写父子选择': lambda c,s,r,m: c[56]['input']['selection'].update(step_id='999'),
    }
    result = {}
    for name, mutate in mutations.items():
        c,s,r,m,l = map(deepcopy, (cases,sources,reviews,manifest,lineage))
        mutate(c,s,r,m)
        try:
            validate(c,s,r,m,l)
        except (ValueError, KeyError, StopIteration):
            result[name] = 'rejected'
        else:
            raise ValueError('损坏探针未拒绝：' + name)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probes', action='store_true'); args = parser.parse_args()
    def no_network(event, _):
        if event in ('socket.connect', 'socket.getaddrinfo'):
            raise RuntimeError('离线检查禁止网络')
    sys.addaudithook(no_network)
    manifest = read(DATA / 'manifest.json'); lineage = read(DATA / 'lineage.json')
    for path, checksum in manifest['files_sha256'].items():
        require(digest((DATA / path).read_bytes()) == checksum, '候选文件哈希变化：' + path)
    for path, checksum in manifest['implementation_sha256'].items():
        require(digest((ROOT / path).read_bytes()) == checksum, '构建/检查入口变化：' + path)
    for path, checksum in lineage['base_files_sha256'].items():
        require(digest((BASE / path).read_bytes()) == checksum, '旧v1文件变化：' + path)
    require(digest((CORPUS / 'manifest.json').read_bytes()) == manifest['corpus_manifest_sha256'], '语料版本不符')
    cases = rows(DATA / 'cases.jsonl'); reviews = rows(DATA / 'reviews.jsonl'); sources = read(DATA / 'sources.json')['sources']
    require(read(DATA / 'sources.json')['current_live_verification'] is False, '不能宣称实时核验')
    result = dict(cases=100, model_calls=0, executed_cases=0, human_approved=0, independent=False)
    result.update(check_corpus()); result.update(verify_sources(sources))
    result.update(validate(cases,sources,reviews,manifest,lineage))
    require((DATA / 'REVIEW.md').read_text(encoding='utf-8') == render_review(cases,sources,reviews), '可读视图不符')
    if args.probes:
        result['damage_probes'] = damage_probes(cases,sources,reviews,manifest,lineage)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
