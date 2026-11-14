"""Freeze seven question-scope corrections; preserve v2 sources and results."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json

from scripts.build_manufacturing_gold_100_v2 import ROOT, canonical, digest, read, rows, write

BASE = ROOT / 'evals/manufacturing_gold_100_v2'
DATA = ROOT / 'evals/manufacturing_gold_100_v3'
REVISIONS = {
    6: '只问是否命中超过4小时条款；重新开工语境及其他触发条件改为可选，禁止无条件恢复的结论不变。',
    7: '问句已给出停机5小时后重新开工；触发原因改为可选，重新首件、数量、必检及签署要求仍为必答。',
    11: '直接回答A18不能套用A17变更；OP-20细分范围及另查独立文件改为可选。',
    19: '保留所选卡身份、实际开工未知及不得跨卡推断；未问的Open状态改为可选。',
    64: '保留两个记录身份、各自时刻及不同的结论；完整日期和时区改为可选。',
    72: '保留编写日期不等于批准日期、变更后重读另建冻结及不能覆盖旧快照；未问的待审核状态改为可选。',
    90: '与072采用相同范围口径；待审核状态改为可选，日期与历史快照边界不变。',
}
CASE_IDS = tuple(f'MFG-GOLD-{i:03}' for i in REVISIONS)
POLICY = {
    'schema_version': 'manufacturing-gold-review-policy-v3',
    'required': '逐项核对问句明确要求的事实、对象、条件及必要业务边界；允许语义等价，不要求照抄。',
    'optional': '未询问的状态、精度、日期时区、后续建议及题设前提的重复说明不作为额外必答。',
    'truthfulness': '可选项未提及不扣分；主动补充的事实仍须有对应来源且不得错误归属、矛盾或越界。',
    'forbidden': '禁止结论优先于要点命中；未知不得解释为确定未执行，正确结论不能抵消错误事实。',
    'evidence': '引用存在不等于语义正确，须核对资料版本、对象、原文或字段及引用用途。',
    'premises': '问题已明确给出的前提可以承接；不得把未复述前提等同于否认前提。复述规则时不能改变条件。',
    'result': '结构契约、逐项必答、禁止结论及补充事实分别记录；任一必要检查失败整题不通过。',
    'provenance': '助手非独立开发回归复核，不是盲测、独立人工、工厂或平台黄金样本批准；旧分不重算或拼接。',
}


def revise(original):
    cases = deepcopy(original)
    for number in REVISIONS:
        x = cases[number - 1]['expected']
        old = x['required_facts']
        optional = deepcopy(x.get('optional_facts', []))
        def fact(text, evidence=None):
            return dict(text=text, evidence_ids=evidence or old[0]['evidence_ids'])
        def extra(text, reason, evidence=None):
            optional.append(dict(fact(text, evidence), reason=reason))
        if number == 6:
            required = [fact('恰好4小时不满足超过4小时，不能仅凭恰好4小时认定命中该条款')]
            extra('该停机条款针对连续停机超过4小时后重新开工', '问句只询问阈值；主动复述条款不得改变其条件。')
            extra('新工单、更换治具等其他触发条件仍须另行核对', '未询问全部触发条件，不要求主动枚举。')
            answer = '不会仅因恰好4小时命中该条款：恰好4小时不属于超过4小时。此判断限于该阈值，不是恢复作业许可。'
        elif number == 7:
            required = [fact(t) for t in ('须重新进行首件确认', '连续检查3件', '所有必检项目全部合格', '由质量人员签署后才能恢复后续作业')]
            extra('5小时超过4小时且题设为重新开工，命中停机触发条件', '问题已经明确给出适用版本和重新开工前提，不强制重复触发理由。')
            answer = '须重新首件确认，连续检查3件，所有必检项目全部合格，并由质量人员签署后才能恢复后续作业。'
        elif number == 11:
            required = [fact('HC-A18不在该HC-A17变更范围，不能直接套用')]
            extra('通知适用范围为HC-A17的OP-20', '未询问通知完整适用范围。')
            extra('HC-A18应查询其独立指导书', '未询问后续查找或办理步骤。')
            answer = '不能直接套用；HC-A18不属于该HC-A17变更的适用范围。'
        elif number == 19:
            required = [deepcopy(old[i]) for i in (0, 2, 3)]
            extra('所选PO-JOB00009的状态为Open', '问题询问能否认定实际开工，没有询问状态枚举。', ['E02'])
            answer = '当前所选PO-JOB00009的实际开工未知，不能因同工单另一张卡已登记开工就认定本卡已开工；未知也不等于确定未执行。'
        elif number == 64:
            required = [fact(t) for t in ('第一工序子行为DEMO-ROW-C31-1，对应OP-30', '该子行实际开工08:12:00',
                '第一作业卡为DEMO-JC-C31-1', '该卡实际开工08:17:00', '两层级分别取值，实际开工时间不同')]
            extra('台账日期为2026-11-04，时区UTC+08:00', '只问几点及是否相同，不强制复述完整日期和时区。')
            answer = '第一子行为DEMO-ROW-C31-1（OP-30），实际开工08:12:00；第一卡为DEMO-JC-C31-1，实际开工08:17:00。两层级独立取值，时间不同。'
        else:
            required = [fact(t) for t in ('作者编写日期不能直接当作批准日期',
                '修订变化后重新读取并另建冻结版本，保留旧版本', '不能直接回填或覆盖旧快照')]
            extra('通知仍为待审核的草稿状态', '问题没有询问当前审核状态，不要求额外补充状态。')
            answer = '编写日期不能直接当作批准日期。修订变化后应重新读取并另建冻结版本，保留旧版本，不能回填或覆盖旧快照。'
        x['reference_answer'] = answer
        x['required_facts'] = [dict(id=f'F{i:02}', **{k:v for k,v in f.items() if k != 'id'}) for i,f in enumerate(required, 1)]
        x['optional_facts'] = [dict(id=f'O{i:02}', **{k:v for k,v in f.items() if k != 'id'}) for i,f in enumerate(optional, 1)]
    return cases


def render(cases, sources, reviews):
    notes = {r['case_id']:r['rationale'] for r in reviews}
    lines = ['# 制造业百题v3候选逐题复核', '',
        '已知v2结果后的助手非独立范围修订；独立人工、工厂及平台批准均0。此候选构建不执行问答，不重算旧成绩。', '']
    for c in cases:
        x = c['expected']
        lines += ['## ' + c['id'], '', '**问题：** ' + c['question'], '', '**标准答案：** ' + x['reference_answer'], '',
            '**模式：** ' + c['test_mode'], '', '**前置：** ' + '；'.join(c['input']['preconditions']), '',
            '**选择：** `' + json.dumps(c['input']['selection'], ensure_ascii=False) + '`', '', '**必答点：**', '']
        lines += [f"- {f['id']}：{f['text']}；证据 " + '、'.join(f['evidence_ids']) for f in x['required_facts']]
        lines += ['', '**可选说明：**', '']
        lines += [f"- {f['id']}：{f['text']}；{f['reason']}；证据 " + '、'.join(f['evidence_ids']) for f in x.get('optional_facts', [])] or ['- 无']
        lines += ['', '**禁止结论：** ' + '；'.join(x['forbidden_claims']), '', '**来源证据：**', '']
        for e in c['evidence']:
            if e['kind'] == 'text':
                lines += [f"- {e['id']} / {e['source_id']} / {e['unit_id']}", '', '```text', e['quote'], '```', '']
            elif e['kind'] == 'field':
                lines += [f"- {e['id']} / {e['source_id']} / `{e['field_path']}` = `{json.dumps(e['value'], ensure_ascii=False)}`；历史投影，非实时核验。"]
            else:
                lines += [f"- {e['id']} / {e['source_id']}：全文条目 {e['unit_ids']}；{e['reason']}"]
        if c['input']['conversation_steps']:
            lines += ['', '**待隔离执行的协议：**', '', '```json', json.dumps(c['input']['conversation_steps'], ensure_ascii=False, indent=2), '```']
        lines += ['', '**助手非独立复核：** ' + notes[c['id']], '']
    lines += ['## 来源索引', '']
    for sid,s in sources.items():
        lines.append(f"- {sid}：[原件](../../{s['repository_path']})" if 'repository_path' in s else f'- {sid}：历史投影，非本轮实时核验。')
    return '\n'.join(lines) + '\n'


def main():
    if DATA.exists():
        raise SystemExit('v3已存在，不覆盖冻结候选。')
    before = rows(BASE / 'cases.jsonl'); cases = revise(before)
    reviews = deepcopy(rows(BASE / 'reviews.jsonl'))
    for c,r in zip(cases,reviews):
        if c['id'] in CASE_IDS:
            r.update(reviewed_at=datetime.now(timezone.utc).isoformat(), rationale=REVISIONS[int(c['id'][-3:])],
                scope='v3_question_scope_review_after_known_v2_results_non_independent')
    write(DATA / 'cases.jsonl', ''.join(json.dumps(c,ensure_ascii=False,separators=(',', ':'))+'\n' for c in cases), raw=True)
    write(DATA / 'reviews.jsonl', ''.join(json.dumps(r,ensure_ascii=False,separators=(',', ':'))+'\n' for r in reviews), raw=True)
    write(DATA / 'sources.json', read(BASE / 'sources.json'))
    write(DATA / 'review_policy.json', POLICY)
    write(DATA / 'lineage.json', dict(base_dataset=BASE.relative_to(ROOT).as_posix(),
        base_files_sha256={p.name:digest(p.read_bytes()) for p in BASE.iterdir() if p.is_file()},
        scope_revised_cases=CASE_IDS, known_results='evals/manufacturing_gold_100_live_v2/runs/20261112-r1',
        old_scores_recomputed=False, independent_holdout=False, changes=[dict(case_id=c['id'],
            old_case_sha256=digest(canonical(b)),new_case_sha256=digest(canonical(c)),reason=REVISIONS[int(c['id'][-3:])])
            for b,c in zip(before,cases) if b != c]))
    write(DATA / 'REVIEW.md', render(cases,read(BASE/'sources.json')['sources'],reviews), raw=True)
    write(DATA / 'README.md', '# 制造业100题黄金候选v3\n\n保留100题及30/20/20/20/10分类，只修订006、007、011、019、064、072、090的必答/可选范围和标准答案；问句、业务选择、来源证据和禁止结论不变。其余93题原样保留。\n\n修订依据是问句和原文，不是将旧回答改判为通过。已知v2结果，助手非独立复核，独立人工、工厂及平台批准均0，不是盲测。候选构建执行0题、模型调用0，无新准确率；旧82/100和全部历史成绩保留，不拼分。后续单独7题运行不代表新版百题全量成绩。\n\n详见[逐题标准与证据](REVIEW.md)、[修订关系](lineage.json)及[复核口径](review_policy.json)。沿用v2的55份逻辑语料，不修改原件，不新增索引、审核发布或运行库授权。\n\n```powershell\n.venv/Scripts/python.exe -m scripts.check_manufacturing_gold_100_v3 --probes\n```\n', raw=True)
    files = ['cases.jsonl','reviews.jsonl','sources.json','review_policy.json','lineage.json','REVIEW.md','README.md']
    write(DATA / 'manifest.json', dict(schema_version='manufacturing-gold-100-manifest-v3', dataset='manufacturing_gold_100_v3',
        status='candidate_assistant_reviewed_non_independent', cases=100, categories=dict(Counter(c['category'] for c in cases)),
        assistant_reviewed_cases=100,human_approved_cases=0,professional_factory_approved_cases=0,platform_approved_cases=0,
        executable=False,live_verified=False,model_calls=0,executed_cases=0,old_results_combined=False,
        files_sha256={p:digest((DATA/p).read_bytes()) for p in files},
        implementation_sha256={p:digest((ROOT/p).read_bytes()) for p in ('scripts/build_manufacturing_gold_100_v3.py','scripts/check_manufacturing_gold_100_v3.py')}))
    print(json.dumps(dict(cases=100,scope_revised=7,model_calls=0)))


if __name__ == '__main__':
    main()
