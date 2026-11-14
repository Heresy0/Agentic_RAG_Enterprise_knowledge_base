"""Question/scope corrections reviewed against the question and original evidence.

No response-based scoring, model answers, runtime approvals, or execution results.
"""
from copy import deepcopy

REVISION_NOTES = {
    8: '仅问外径范围、端点、量具；每件读数改为可选。',
    18: '与019一致：未询问精度，保留卡编号、状态、实际开工未知为必答，精度改为可选。',
    19: '仅问当前卡能否认定已开工；精度改为可选，未知与未开工仍严格区分。',
    44: '保留检查要求、超限判断及不能放行；未问后续签署办理，分别保存改为可选。',
    45: '围绕计划不能代实际及不能单凭计划选版；登记值和子级补值边界改为可选。',
    46: '明确询问两个记录编号、时刻及是否相同；工序代码、完整日期/时区改为可选。',
    61: '保留完整安全要求，将缺项处理、变更后的复核动作和程序同名边界明确写入问题，拆开必答点。',
    63: '与045统一问句范围；子级补值和当前登记值不再作为额外必答。',
    79: '保留全部原标准，明确询问独立核对、登记、导通推论与换映射首件，拆开必答点。',
    80: '与044统一：不合格原因和不能放行为必答，后续记录/签署分别保存为可选。',
    81: '与045/063统一问句范围，保留禁止用计划或子级推定实际开工。',
}


def points(rows, prefix):
    return [dict(id=f'{prefix}{i:02}', text=text, evidence_ids=evidence,
                 **({'reason': reason} if reason else {}))
            for i, (text, evidence, reason) in enumerate(rows, 1)]


def revise_cases(original):
    cases = deepcopy(original)
    for number in REVISION_NOTES:
        case = cases[number - 1]; expected = case['expected']
        old = expected['required_facts']
        required = [(f['text'], f['evidence_ids'], None) for f in old]
        optional = [(f['text'], f['evidence_ids'], f.get('reason')) for f in expected.get('optional_facts', [])]
        if number == 8:
            required = required[:3]
            optional.append((old[3]['text'], old[3]['evidence_ids'], '未询问读数保存要求。'))
            expected['reference_answer'] = '外径19.95至20.05 mm，包含上下限，使用MIC-025。可另说明每件原始读数须记录，但不作本题必答。'
        elif number in (18, 19):
            required[2] = ('当前所选卡的实际开工未知' + ('，不能认定已开工' if number == 19 else ''), old[2]['evidence_ids'], None)
            optional.append(('当前卡的开工时间精度未知', old[2]['evidence_ids'], '未询问精度。'))
        elif number in (44, 80):
            required[2] = ('该样例不能直接判合格或质量放行，应保持待复核或待处置', old[2]['evidence_ids'], None)
            optional.append(('操作员记录、质量复核结论和放行签署分别保存', old[2]['evidence_ids'], '后续记录及签署流程未被询问。'))
        elif number in (45, 63, 81):
            required = [
                ('计划开工不得当作工单实际开工', old[1]['evidence_ids'], None),
                ('不能只凭计划时间选版，还须核对产品、工序、有效区间和已审核适用性', old[2]['evidence_ids'], None),
            ]
            optional += [
                ('本台账的工单实际开工未登记', old[0]['evidence_ids'], '未询问实际登记值。'),
                ('子级记录也不能补填工单级实际开工', old[1]['evidence_ids'], '未询问子级时间替代。'),
            ]
            expected['reference_answer'] = '不能把计划开工当作工单实际开工，也不能单凭计划时间决定指导书版本；仍须核对产品、工序、有效区间和已审核适用性。可补充当前工单未登记实际开工、子级不能代填，但不作额外必答。'
        elif number == 46:
            case['question'] = '仅按DEMO-B21合成资料，第一工序子行与第一作业卡的编号各是什么，实际开工各在几点，是否相同？'
            evidence = old[0]['evidence_ids']
            required = [(t, evidence, None) for t in [
                '工序子行编号DEMO-ROW-B21-1', '工序子行实际开工08:12:00',
                '作业卡编号DEMO-JC-B21-1', '作业卡实际开工08:17:00',
                '两者分别取值，实际开工时间不相同',
            ]]
            optional += [('该子行对应OP-10', evidence, '未询问工序代码。'),
                         ('同一台账日期、时区UTC+08:00', evidence, '仅问几点和是否相同，不强制完整日期与时区。')]
            expected['reference_answer'] = '工序子行为DEMO-ROW-B21-1，实际开工08:12:00；作业卡为DEMO-JC-B21-1，实际开工08:17:00。两层级独立取值，时间不相同。'
        elif number == 61:
            case['question'] = '仅按DEMO-C31合成资料，OP-40的2.0版须保存哪些记录，缺项怎样处理，能用设备结束时间补质量签署吗？程序版本变更后应核对和复核什么，程序名称相同能否证明版本相同？'
            required = [(t, old[0]['evidence_ids'], None) for t in [
                '保存设备结果', '保存检测程序版本', '保存原始数据记录号',
                '缺少任一项时将该件标记为待复核', '设备运行结束时间不能补写质量签署',
            ]] + [(t, old[1]['evidence_ids'], None) for t in [
                '程序版本变更后先核对受控程序清单', '程序版本变更后进行验证记录复核',
                '程序名称相同不能证明程序版本相同',
            ]]
        elif number == 79:
            case['question'] = '仅按DEMO-D41合成资料，OP-60的2.0版须核对哪些信息、分别登记哪些结果，导通通过能否证明标签内容或针位映射版本正确？映射变更后如何做首件，能否沿用旧映射的通过结论？'
            required = [(t, old[0]['evidence_ids'], None) for t in [
                '核对检测程序版本', '核对针位映射版本', '核对产品序列号',
                '登记导通结果', '登记标签核对结果', '导通通过不能证明标签内容或针位映射版本正确',
            ]] + [(t, old[1]['evidence_ids'], None) for t in [
                '针位映射变更后使用新映射进行首件核对', '旧映射通过记录不能复制为新映射验证结论',
            ]]
        expected['required_facts'] = points(required, 'F')
        expected['optional_facts'] = points(optional, 'O')
    return cases
