"""Local answer guidance; never changes obligations, identity or tool budgets."""
import json
import re

from .query_planner import current_query_plan


def asks_actual_completion_date(question):
    """Keep planned-date and actual-status clauses distinct.

    This only selects generation guidance; it does not classify evidence or
    replace coverage/temporal validators. Never let a date request in a planned
    clause supply the date requirement for a different yes/no status clause.
    """
    for clause in re.split(r'[，,；;？?\n]', question):
        if not re.search(r'哪天|何时|什么时候|日期|时间', clause):
            continue
        if re.search(r'计划|预计|原定|安排', clause):
            # In a mixed clause, only the explicitly actual suffix is eligible.
            actual = re.search(r'实际|已经|已完成', clause)
            if actual is None:
                continue
            clause = clause[actual.start():]
        if (re.search(r'实际|最终|已经|已完成', clause)
                and re.search(r'哪天|何时|什么时候|日期|时间', clause)):
            return True
    return False


def direct_answer_errors(question, draft):
    """Stricter layout completeness, not a semantic truth classifier."""
    # Scope this check to whole-object condition judgments. Per-metric
    # "是否都达标" and multi-part permission questions may legitimately answer
    # through several separate verdicts and are not classified by this check.
    if (not draft.answerable or not re.search(r'是否(?:满足|符合)', question)
            or len([part for part in re.split(r'[？?；;\n]', question) if part.strip()]) > 1):
        return []
    first = draft.claims[0].text if draft.claims else ''
    if not re.search(r'满足|符合|达标|可以|允许|禁止|不得|不能|不可|不行|能够|^能[，。！!]', first):
        return ['缺少首条总体判定结论：不能只列对象绑定、规则或实测事实']
    return []


def answer_contract(question, requirements):
    plan = current_query_plan()
    layout = None
    rules = [
        '按原问题的对象、时期、单位和前提作答；辅助子问题不能增加用户没有要求的任务。',
        '逐项直接回答必答清单，保留必要条件、例外和原因；资料中的相邻流程不是新的必答任务。',
        '能否/是否满足/是否达标类问题必须给出直接结论，再逐项说明依据；只列条件不等于回答判定。',
        '每条结论只绑定实际支持它的资料，数字不能从另一份资料借用；来源有版本时核对适用时期。',
        '日期沿用所引原文的表达：有年份就保留，原文只写月日就只写月日；不得从文件名、元数据或邻近记录补年份，也不额外补充未询问的日期。',
        '未载明禁止不等于允许或豁免，历史规则只按当时明示条件回答。',
        '直接事实不计算；需要推导时只生成回答目标所需的完整计算，不生成重复或中间计算结论。',
    ]
    if re.search(r'每(?:个|一|满|次|日|天)|标准|额度|限额|对应|折算', question):
        rules.append('区分单次/单位标准与实际累计量。仅问标准时不要求实际次数、天数或申请记录；问累计量时不得猜测这些输入。')
    if re.search(r'相同|不同|差异|比较|对比|分别', question):
        rules.append('比较须直接说明相同或不同，并逐个给出对象、数值、口径及依据；不能只列数字省略比较结论。')
    if re.search(r'是否(?:满足|符合|达标)|能否|可否', question):
        rules.append('claims[0]必须做出总体判定，直接说明整个被问对象是否满足/符合/达标，并引用适用规则、对象/版本绑定及实测证据；claims[1:]再逐项核对条件。只列实测事实或说采用某规则，不能代替判定，也不能代替引用规则正文。')
        layout = dict(first_claim='明确整体满足或不满足，不是某个局部条件的符合情况',
                      first_claim_sources='实际适用的规则正文、对象/版本绑定、实测资料的已有编号',
                      remaining_claims='逐项给出判定的必要依据，不能把某个保留条件当成全部条件')
    if re.search(r'去重|视为同一|合并|唯一', question):
        rules.append('对象身份判定须解释原文的身份范围和完整标识组合，保留构成组合键的所有字段原名；不能仅回答不合并而省略为什么，不能省略同一标识在不同范围下的区别。')
    if re.search(r'计划|预计|安排', question) and re.search(r'是否|实际|已经|已完成|已修复', question):
        rules.append('分别回答计划和实际状态。原文明示“计划不代表已完成”等边界时，可引用原文回答“这些资料不能确认已完成”，不能改称“实际未完成”或“已完成”。')
        rules.append('若原文明示计划不是完成证明，回答计划及“现有资料无法确认已完成”即回答了这两个要点：answerable=true，两项coverage均answered且关联对应结论，evidence_gap=null。这不是断言实际未完成。仅有计划但没有状态或边界依据时仍按缺证据拒答，不伪造完整覆盖。')
        if not asks_actual_completion_date(question):
            layout = dict(mode='计划与证据边界；不是索要实际完成日期',
                example_is_format_only='以下占位文本不能作为企业事实，必须替换为本次资料与必答编号',
                example=dict(answerable=True, response_type='answer', evidence_gap=None, refusal_reason=None,
                    claims=[dict(text='原文明示的计划时间', citations=['原文计划所在的资料编号']),
                            dict(text='原文明示该计划不能证明已经完成，因此这些证据不能确认实际完成',
                                 citations=['原文边界声明所在的资料编号'])],
                    coverage=[dict(requirement_id='对应必答编号', aspect='计划时间', status='answered', claim_indices=[1], missing_information=None, source_quote='原文计划的连续摘录'),
                              dict(requirement_id='对应必答编号', aspect='是否已完成的证据边界', status='answered', claim_indices=[2], missing_information=None, source_quote='原文证据边界的连续摘录')]))
    if asks_actual_completion_date(question):
        rules.append('用户索要实际完成日期时，必须有实际完成证据与日期。“没有日期/资料不能确认”不是所需日期，不能标为完整可回答；仅有计划或证据边界时answerable=false、claims=[]。')
    if re.search(r'原因|根因|为何|为什么|导致', question):
        rules.append('解释实际因果机制，保留原文明确关联的变更/事件编号及最终结果，不能以“参照复盘”代替解释。')
    return json.dumps(dict(
        intent=plan.intent if plan else None,
        strategy=plan.strategy if plan else None,
        requirement_ids=[row['id'] for row in requirements],
        guidance=rules,
        **({'response_layout': layout} if layout else {}),
    ), ensure_ascii=False)
