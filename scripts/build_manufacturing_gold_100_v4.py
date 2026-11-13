"""Unify question scope across 100 cases; preserve all historical candidates."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json

from scripts.build_manufacturing_gold_100_v2 import ROOT, canonical, digest, read, rows, write
from scripts.build_manufacturing_gold_100_v3 import POLICY as V3_POLICY, render as render_v3

BASE = ROOT / 'evals/manufacturing_gold_100_v3'
DATA = ROOT / 'evals/manufacturing_gold_100_v4'
REVISIONS = {
    1: '问句已给出9月工单开工，具体日期改可选；旧工单沿用1.0及连续2件仍必答。',
    4: '只问更换治具能否沿用结论，必答不能沿用且重新首件；未问的后续数量与签署流程改可选，错误补充仍失败。',
    10: '不能直接取得所问参数仍必答；未问的取得批准文件建议改可选。',
    13: '工单、型号、工序代码和卡仍必答；未要求的工序子行内部ID改可选。',
    17: '状态、实际开工及精度未知仍必答；已给出的OP-30及未问内部ID改可选。',
    25: '步骤名称不能证明工单原始字段、原始字段未知仍必答；平台映射编码解释改可选。',
    26: '指导书编号/2.0和已复核分配依据仍必答；未问的内部修订号7和工艺编码解释改可选。',
    43: '保存核对要求及两种变更触发仍必答；未问的批量放行签署条款单列可选。',
    52: '拟分配不等于平台已审核仍必答；待审核/未登记细分状态和完整办理路径改可选，与070/088统一。',
    54: '编写日期不等于批准、新冻结且保留旧快照仍必答；未问待审状态改可选，与072/090统一。',
    64: '子行/卡身份、各自时刻和不同仍必答；未问OP-30改可选，与046/082统一。',
    70: '与052/088统一：拟分配不能当已审核依据，细分状态和完整办理路径不强制复述。',
    82: '与046/064统一：身份、各自时刻和不同仍必答，工序码、日期及时区改可选。',
    88: '与052/070统一：拟分配不能当已审核依据，细分状态和完整办理路径不强制复述。',
}
CASE_IDS = tuple(f'MFG-GOLD-{n:03}' for n in REVISIONS)
POLICY = dict(V3_POLICY, schema_version='manufacturing-gold-review-policy-v4',
    identity='允许承接题设中无歧义的实体和前提；要求列明编号时必须回答，未问的内部ID不强制增加。来源实体、父子关系及字段层级仍须正确。',
    conditions='直接询问检验、恢复或放行要求时，所有适用必要条件仍必答。仅问能否沿用结论时，不强制扩展完整后续流程；主动复述规则不得截断或改变条件。',
    scope='按全部100题同一口径审核，不以旧运行通过与否选择性放宽；完整来源原文不等于每句均为当前问题必答。')


def revise(original):
    cases = deepcopy(original)
    for n in REVISIONS:
        x = cases[n-1]['expected']; old = x['required_facts']
        optional = deepcopy(x.get('optional_facts', []))
        def fact(text, evidence=None):
            return dict(text=text, evidence_ids=evidence or old[0]['evidence_ids'])
        def extra(text, reason, evidence=None):
            optional.append(dict(fact(text,evidence), reason=reason))
        if n == 1:
            required = deepcopy(old[1:])
            extra(old[0]['text'], '题设已明确9月开工，不强制重复完整日期。')
            answer = '没有该工单书面切换通知时沿用1.0版，连续检查2件；不能用作业卡10月开工替换工单开工选版。'
        elif n == 4:
            required = [fact('不能沿用更换前结论，更换定位治具后须重新进行首件确认')]
            extra('按适用1.0版连续检查2件，全部项目合格并由质量人员签署', '未问后续完整流程；若主动补充，不得改变数量、合格或签署条件。')
            answer = '不能沿用更换前的首件结论；更换定位治具后须重新进行首件确认。'
        elif n == 10:
            required = [fact('现有指导书未给出所问参数，不能直接给出主轴转速和进给')]
            extra('取得适用的批准工艺文件后核对', '未询问后续办理步骤；仍禁止编造参数。')
            answer = '不能直接给出；现有指导书未给出所问的主轴转速和进给参数。'
        elif n == 13:
            required = [deepcopy(old[0]),deepcopy(old[1]),fact('工序为OP-20'),deepcopy(old[3])]
            extra('工序子行内部ID为7pvg4cgbg0', '问的是工序身份，没有要求额外列内部子行ID。')
            answer = '工单ERP-WO-EXECUTION、型号HC-A17、工序OP-20、作业卡PO-JOB00008；不能混入其他卡。'
        elif n == 17:
            required = deepcopy(old[1:])
            extra('所选OP-30的子行ID为7pvhaa8m4o', '题设已给工序，未询问内部ID。')
            answer = '所选OP-30状态为Pending，实际开工及时间精度均未知，不能用其他层级时间补齐。'
        elif n == 25:
            required = deepcopy(old[:2])
            extra(old[2]['text'], '问的是源系统原始字段，未询问平台映射编码的办理来源。')
            answer = '不能这样认定。MES工单原始operation_code未知，步骤名称不能当作工单原始工序字段。'
        elif n == 26:
            required = [fact('已复核分配指向HC-WI-A17-020的2.0版'),*deepcopy(old[1:])]
            extra('所引映射修订为7，平台工艺编码为OP-20', '未要求列内部映射修订和编码；来源绑定核验仍保留。')
            answer = '使用HC-WI-A17-020的2.0版，依据是已复核的步骤分配，不是按未知工单开工时间推定。'
        elif n == 43:
            required = [deepcopy(old[0]),fact('更换检具或定位治具后重新进行首件确认',old[1]['evidence_ids'])]
            extra('质量复核签署前不得登记批量放行', '本题问保存/核对及变更触发，没有询问放行条件。',old[1]['evidence_ids'])
            answer = '两个相互垂直方向各测一次并分别保存原始读数，任一超范围时待处置，不能用平均值判合格；更换检具或定位治具后重新首件确认。'
        elif n in (52,70,88):
            required = [fact('拟分配2.0不能当作平台已审核的作业依据，拟分配不等于有效已复核分配',old[1]['evidence_ids'])]
            extra('台账分配为待审核草案，未登记到平台', '未询问细分状态，不强制额外枚举。')
            extra('须核对有效且最新的reviewed分配，不能从名称或子级时间猜版本', '未询问完整办理路径；若补充仍须有来源。',old[1]['evidence_ids'])
            answer = '不能。拟分配2.0不等于平台已经审核的有效步骤指导书分配。'
        elif n == 54:
            required = [fact(t) for t in ('编写日期不能直接当作批准日期','变更后重新读取并另建冻结版本，保留旧版本','不能直接回填或覆盖旧快照')]
            extra('通知仍为待审核草稿', '未询问当前审核状态，与072/090统一。')
            answer = '编写日期不能当批准日期；变更后重读并另建冻结，保留旧版本，不能回填或覆盖旧快照。'
        else:
            family,op,date = ('C31','OP-30','2026-11-04') if n==64 else ('D41','OP-50','2026-11-06')
            required = [fact(t) for t in (f'第一子行为DEMO-ROW-{family}-1','该子行实际开工08:12:00',f'第一卡为DEMO-JC-{family}-1','该卡实际开工08:17:00','两层级分别取值，实际开工时间不同')]
            extra(f'子行对应{op}', '编号已唯一标识实体，未另问工序码。')
            if n==82:
                extra(f'台账日期为{date}，时区UTC+08:00', '只问几点，不强制复述完整日期和时区。')
            answer = f'第一子行DEMO-ROW-{family}-1实际开工08:12:00，第一卡DEMO-JC-{family}-1实际开工08:17:00；两层级分别取值，时间不同。'
        x.update(reference_answer=answer,
            required_facts=[dict(id=f'F{i:02}',**{k:v for k,v in f.items() if k!='id'}) for i,f in enumerate(required,1)],
            optional_facts=[dict(id=f'O{i:02}',**{k:v for k,v in f.items() if k!='id'}) for i,f in enumerate(optional,1)])
    return cases


def render(cases,sources,reviews):
    return render_v3(cases,sources,reviews).replace('百题v3候选','百题v4候选').replace('已知v2结果后','已知v3结果后')


def scope_reason(case):
    n=int(case['id'][-3:])
    if n in REVISIONS:return REVISIONS[n]
    if n in (2,3,5,7,12,27,41,44,59,61,62,77,79,80):
        return '问句明确询问作业、检验、恢复、放行或版本比较；保留对应完整必要条件、记录缺项和独立签署边界。'
    if case['category'] in ('safety','conversation'):
        return '问句测试权限、来源、安全或会话协议，保留拒绝、父子选择、不可覆盖历史和调用前停止等必要边界。'
    return '逐项对照问句和既有来源；所列事实属于所问对象、字段、版本、时刻或必要证据边界，未发现需新增的可选化调整。'


def main():
    if DATA.exists():raise SystemExit('v4已存在，不覆盖冻结候选。')
    before=rows(BASE/'cases.jsonl');cases=revise(before);reviews=deepcopy(rows(BASE/'reviews.jsonl'))
    now=datetime.now(timezone.utc).isoformat();audit=[]
    for c,r in zip(cases,reviews):
        reason=scope_reason(c)
        r.update(reviewed_at=now,rationale=reason,scope='v4_all_100_question_scope_review_after_known_results_non_independent')
        audit.append(dict(case_id=c['id'],action='scope_revised' if c['id'] in CASE_IDS else 'kept',reason=reason,
            reviewer_type='assistant',independent=False,human_approved=False,evidence_ids=[e['id'] for e in c['evidence']]))
    for name,value in [('cases.jsonl',cases),('reviews.jsonl',reviews),('scope_audit.jsonl',audit)]:
        write(DATA/name,''.join(json.dumps(c,ensure_ascii=False,separators=(',',':'))+'\n' for c in value),raw=True)
    write(DATA/'sources.json',read(BASE/'sources.json'));write(DATA/'review_policy.json',POLICY)
    write(DATA/'lineage.json',dict(base_dataset=BASE.relative_to(ROOT).as_posix(),
        base_files_sha256={p.name:digest(p.read_bytes()) for p in BASE.iterdir() if p.is_file()},
        scope_reviewed_cases=100,scope_revised_cases=CASE_IDS,known_results='evals/manufacturing_gold_100_live_v3/runs/20261112-r1',
        old_scores_recomputed=False,independent_holdout=False,changes=[dict(case_id=c['id'],old_case_sha256=digest(canonical(b)),
        new_case_sha256=digest(canonical(c)),reason=REVISIONS[int(c['id'][-3:])]) for b,c in zip(before,cases) if b!=c]))
    write(DATA/'REVIEW.md',render(cases,read(BASE/'sources.json')['sources'],reviews),raw=True)
    write(DATA/'README.md','# 制造业100题黄金候选v4\n\n对全部100题统一审核问句范围，14题修订必答/可选及参考答案，86题原样保留。问句、业务选择、证据、禁止结论、分类和资料原件均不变。\n\n未问的状态、内部ID、日期时区或后续建议不强制增加；主动补充仍须正确，所问检验/恢复/放行的完整条件仍必答。062签署、002完整条件、009/044/084的事实真实性及全部权限来源边界没有放宽。\n\n已知历史输出后的助手非独立复核，独立人工/工厂/平台批准0，非盲测；构建执行0题、模型调用0，不重算旧82/100或78/100。\n\n见[逐题标准](REVIEW.md)、[百题范围审核](scope_audit.jsonl)、[版本关系](lineage.json)与[复核口径](review_policy.json)。运行结果另建目录，不修改此候选的执行状态。\n\n```powershell\n.venv/Scripts/python.exe -m scripts.check_manufacturing_gold_100_v4 --probes\n```\n',raw=True)
    files=['cases.jsonl','reviews.jsonl','scope_audit.jsonl','sources.json','review_policy.json','lineage.json','REVIEW.md','README.md']
    write(DATA/'manifest.json',dict(schema_version='manufacturing-gold-100-manifest-v4',dataset='manufacturing_gold_100_v4',
        status='candidate_assistant_reviewed_non_independent',cases=100,categories=dict(Counter(c['category'] for c in cases)),
        assistant_reviewed_cases=100,human_approved_cases=0,professional_factory_approved_cases=0,platform_approved_cases=0,
        executable=False,live_verified=False,model_calls=0,executed_cases=0,old_results_combined=False,
        files_sha256={p:digest((DATA/p).read_bytes()) for p in files},implementation_sha256={p:digest((ROOT/p).read_bytes()) for p in
        ('scripts/build_manufacturing_gold_100_v4.py','scripts/check_manufacturing_gold_100_v4.py')}))
    print(json.dumps(dict(cases=100,scope_reviewed=100,scope_revised=len(REVISIONS),model_calls=0)))


if __name__=='__main__':main()
