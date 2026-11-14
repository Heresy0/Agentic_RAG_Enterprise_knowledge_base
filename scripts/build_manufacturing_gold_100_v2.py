"""Create an offline v2 candidate and three corrected text originals, never run QA."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from scripts.manufacturing_gold_100_v2_data import REVISION_NOTES, revise_cases

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'evals/manufacturing_gold_100_v1'
DATA = ROOT / 'evals/manufacturing_gold_100_v2'
CORPUS = ROOT / 'sample_docs/manufacturing_zh_corrections_v2'
BASE_CORPUS = ROOT / 'sample_docs/manufacturing_zh_expansion_v1'
REVISED_SOURCES = ('ZH-B21-08', 'ZH-C31-08', 'ZH-D41-08')
COUNTS = dict(guidance=30, erpnext=20, openmes=20, safety=20, conversation=10)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def rows(path):
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines()]


def write(path, value, *, raw=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as f:
        f.write(value if raw else json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def correct_time(text, date):
    # Only the known malformed full timestamps are corrected; business values,
    # date-only fields and the explicit same-day convention are unchanged.
    for clock in ('08:00:00', '08:12:00', '09:00:00'):
        text = text.replace(date + clock + '+08:00', date + 'T' + clock + '+08:00')
    return text


def render_review(cases, sources, reviews):
    notes = {r['case_id']: r['rationale'] for r in reviews}
    lines = ['# 制造业100题v2候选逐题复核', '',
        '助手非独立复核；人工、工厂及运行平台批准均0。全程零模型，没有本轮问答或准确率。', '',
        '本版是已知v1开发结果之后的修订候选，不是独立盲测。原v1及69/100历史成绩保留，不换口径重算。', '']
    for c in cases:
        x = c['expected']
        lines += ['## ' + c['id'], '', '**问题：** ' + c['question'], '',
            '**标准答案：** ' + x['reference_answer'], '', '**模式：** ' + c['test_mode'], '',
            '**前置：** ' + '；'.join(c['input']['preconditions']), '',
            '**选择：** `' + json.dumps(c['input']['selection'], ensure_ascii=False) + '`', '', '**必答点：**', '']
        lines += ['- ' + f['id'] + '：' + f['text'] + '；证据 ' + '、'.join(f['evidence_ids']) for f in x['required_facts']]
        lines += ['', '**可选说明（不计必答）：**', '']
        lines += ['- ' + f['id'] + '：' + f['text'] + '；' + f.get('reason', '') for f in x.get('optional_facts', [])] or ['- 无']
        lines += ['', '**禁止结论：** ' + '；'.join(x['forbidden_claims']), '', '**来源证据：**', '']
        for e in c['evidence']:
            source = sources[e['source_id']]
            if e['kind'] == 'text':
                unit = next(u for u in source['units'] if u['id'] == e['unit_id'])
                lines += [f"- {e['id']} / {e['source_id']} / {e['unit_id']}；物理页 {unit.get('physical_pages', [])}",
                          '', '```text', e['quote'], '```', '']
            elif e['kind'] == 'field':
                lines += [f"- {e['id']} / {e['source_id']} / `{e['field_path']}` = `{json.dumps(e['value'],ensure_ascii=False)}`；历史投影，非实时读取。"]
            else:
                lines += [f"- {e['id']} / {e['source_id']}：全文条目 {e['unit_ids']}；{e['reason']}"]
        if c['input']['conversation_steps']:
            lines += ['', '**待隔离执行的协议：**', '', '```json',
                      json.dumps(c['input']['conversation_steps'], ensure_ascii=False, indent=2), '```']
        lines += ['', '**助手非独立复核：** ' + notes[c['id']], '']
    lines += ['## 来源索引', '']
    for sid, source in sources.items():
        lines.append(f"- {sid}：[原件](../../{source['repository_path']})" if 'repository_path' in source else
                     f"- {sid}：保持不变的历史业务/映射投影，未进行本轮实时核验。")
    return '\n'.join(lines) + '\n'


def main():
    if DATA.exists() or CORPUS.exists():
        raise SystemExit('v2目录已存在；保留已生成版本，不覆盖。')
    inventory = deepcopy(read(BASE_CORPUS / 'sources.json')['documents'])
    originals = {r['id']: deepcopy(r) for r in inventory}
    corrections = []
    from scripts.manufacturing_zh_expansion_data import PRODUCTS
    dates = {p['family']: p['date'] for p in PRODUCTS}
    for record in inventory:
        if record['id'] not in REVISED_SOURCES:
            continue
        old_path = ROOT / record['repository_path']
        original = old_path.read_bytes()
        assert digest(original) == record['sha256']
        text = original.decode('utf-8')
        corrected = correct_time(text, dates[record['source_family']])
        assert corrected != text and corrected.count('T08:00:00+08:00') == 1
        path = CORPUS / 'documents' / old_path.name
        write(path, corrected, raw=True)
        record.update(repository_path=path.relative_to(ROOT).as_posix(), sha256=digest(path.read_bytes()),
            bytes=path.stat().st_size, format_revision=2,
            supersedes=dict(repository_path=originals[record['id']]['repository_path'], sha256=digest(original)))
        for unit in record['evidence']:
            unit['text'] = correct_time(unit['text'], dates[record['source_family']])
        corrections.append(dict(source_id=record['id'], **record['supersedes'],
            new_repository_path=record['repository_path'], new_sha256=record['sha256'],
            reason='补齐日期与时刻间的ISO 8601分隔符T；不改变业务值、未知状态或实体关系。'))
    write(CORPUS / 'sources.json', dict(edition='manufacturing-zh-corrections-v2', documents=inventory,
        logical_documents=55, new_revised_originals=3, referenced_originals=52, model_calls=0,
        indexed_this_revision=False, published_this_revision=False))
    write(CORPUS / 'lineage.json', dict(base_corpus='sample_docs/manufacturing_zh_expansion_v1',
        base_sources_sha256=digest((BASE_CORPUS / 'sources.json').read_bytes()), corrections=corrections))
    write(CORPUS / 'README.md', '# 中文制造语料格式修订v2\n\n55份逻辑资料仅新增3份TXT修订，引用52份原件。三份台账的日期与时刻改用标准ISO 8601的T分隔；业务含义、样例值、未知状态、同日约定和父子关系不变。\n\n旧v1原件、生成器及历史模型结果全部保留。新入口使用修订后的格式，不修改旧生成器的冻结源码。未索引、未发布、未调用模型或改动数据库/运行环境；不是工厂执行或批准证据。详情见[sources.json](sources.json)与[lineage.json](lineage.json)。\n', raw=True)
    corpus_files = ['sources.json', 'lineage.json', 'README.md', *[r['new_repository_path'].split(CORPUS.relative_to(ROOT).as_posix() + '/')[1] for r in corrections]]
    write(CORPUS / 'manifest.json', dict(edition='manufacturing-zh-corrections-v2', logical_documents=55,
        new_revised_originals=3, referenced_originals=52, model_calls=0, indexed=False,
        files_sha256={p: digest((CORPUS / p).read_bytes()) for p in corpus_files}))
    base_cases = rows(BASE / 'cases.jsonl'); cases = revise_cases(base_cases)
    sources = deepcopy(read(BASE / 'sources.json')['sources'])
    for sid in REVISED_SOURCES:
        revised = next(r for r in inventory if r['id'] == sid)
        source = sources[sid]
        source.update(repository_path=revised['repository_path'], original_sha256=revised['sha256'],
                      format_revision=2, supersedes=revised['supersedes'])
        by_unit = {u['unit_id']: u for u in revised['evidence']}
        for unit in source['units']:
            unit['text'] = by_unit[unit['id']]['text']
    reviews = []; changes = []; base_reviews = {r['case_id']: r for r in rows(BASE / 'reviews.jsonl')}
    stamp = datetime.now(timezone.utc).isoformat()
    for i, case in enumerate(cases, 1):
        for evidence in case['evidence']:
            if evidence['kind'] == 'text' and evidence['source_id'] in REVISED_SOURCES:
                evidence['quote'] = next(u['text'] for u in sources[evidence['source_id']]['units'] if u['id'] == evidence['unit_id'])
        note = REVISION_NOTES.get(i, '问句及判定标准沿用v1；逐项确认来源、对象/版本、字段类型和禁止推论仍一致。')
        if any(e['source_id'] in REVISED_SOURCES for e in case['evidence']):
            note += ' 台账证据同步到标准时间格式修订，字段值与实体含义不变。'
        review = deepcopy(base_reviews[case['id']])
        review.update(reviewed_at=stamp, rationale=note,
            scope='v2_question_source_scope_review_after_known_v1_results_not_independent_or_runtime_approval')
        reviews.append(review)
        if case != base_cases[i - 1]:
            changes.append(dict(case_id=case['id'], scope_revision=i in REVISION_NOTES,
                old_case_sha256=digest(canonical(base_cases[i - 1])), new_case_sha256=digest(canonical(case)), reason=note))
    assert Counter(c['category'] for c in cases) == COUNTS
    write(DATA / 'cases.jsonl', ''.join(json.dumps(c, ensure_ascii=False, separators=(',', ':')) + '\n' for c in cases), raw=True)
    write(DATA / 'reviews.jsonl', ''.join(json.dumps(r, ensure_ascii=False, separators=(',', ':')) + '\n' for r in reviews), raw=True)
    write(DATA / 'sources.json', dict(schema_version='manufacturing-gold-100-sources-v2',
        current_live_verification=False, sources=sources))
    write(DATA / 'lineage.json', dict(base_dataset='evals/manufacturing_gold_100_v1',
        base_files_sha256={p.name: digest(p.read_bytes()) for p in sorted(BASE.iterdir()) if p.is_file()},
        scope_revised_cases=[f'MFG-GOLD-{i:03}' for i in REVISION_NOTES], changes=changes,
        known_v1_results='evals/manufacturing_gold_100_live_v1/runs/20261112-r1',
        v1_scores_recomputed=False, independent_holdout=False))
    write(DATA / 'REVIEW.md', render_review(cases, sources, reviews), raw=True)
    write(DATA / 'README.md', '# 制造业100题黄金候选v2\n\n保留100题及30/20/20/20/10分类。11题按问句范围修订或澄清（含逐题核对时发现与019同样未问精度的018），三份台账引用标准ISO时间格式新原件；逐项变更见[lineage.json](lineage.json)，全部问题、标准答案、必答/可选点与来源见[REVIEW.md](REVIEW.md)。\n\n助手完成非独立复核；独立人工、工厂和运行平台批准均0。v1结果已知，本版不是独立盲测；执行题目0、模型调用0，无新准确率，不把原69/100改判或拼分。v1题集、原文和历史成绩不变。\n\n仍包含54道离线资料题、6道待绑定真实隔离ID的协议题及40道继承场景。离线别名不能直接请求上游；未执行本轮权限/来源实时核验、审核、冻结、索引或容器更新。已有v1运行器固定读取v1，不可将它当作v2执行入口；后续模型运行须另建绑定、批次和预算。\n\n```powershell\n.venv/Scripts/python.exe -m scripts.check_manufacturing_gold_100_v2 --probes\n```\n', raw=True)
    names = ['cases.jsonl', 'reviews.jsonl', 'sources.json', 'lineage.json', 'REVIEW.md', 'README.md']
    implementations = ['scripts/build_manufacturing_gold_100_v2.py', 'scripts/manufacturing_gold_100_v2_data.py',
                       'scripts/check_manufacturing_gold_100_v2.py']
    write(DATA / 'manifest.json', dict(schema_version='manufacturing-gold-100-manifest-v2', dataset='manufacturing_gold_100_v2',
        status='candidate_assistant_reviewed_non_independent', cases=100, categories=COUNTS,
        assistant_reviewed_cases=100, human_approved_cases=0, professional_factory_approved_cases=0, platform_approved_cases=0,
        executable=False, live_verified=False, model_calls=0, executed_cases=0, old_results_combined=False,
        test_modes=dict(Counter(c['test_mode'] for c in cases)),
        corpus_manifest_sha256=digest((CORPUS / 'manifest.json').read_bytes()),
        files_sha256={p: digest((DATA / p).read_bytes()) for p in names},
        implementation_sha256={p: digest((ROOT / p).read_bytes()) for p in implementations}))
    print(json.dumps(dict(cases=100, scope_revised=len(REVISION_NOTES), changed_records=len(changes), revised_originals=3, model_calls=0)))


if __name__ == '__main__':
    main()
