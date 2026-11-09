"""Conservative recognition of explicit operations in the read-only QA entry."""
import re


READ_ONLY_REFUSAL = (
    "本平台仅提供只读查询与作业指导，不能代为执行质量放行、质量签署、恢复生产或修改 MES/ERP 记录。"
    "报工、规则和登记状态不能代替实际检验、质量签署或恢复批准；请由有权限的人员核验记录并在业务系统办理。"
)

_ACTION = re.compile(
    r"(?:质量)?放行|恢复(?:生产|作业)|(?:完成|代签|补签)?质量签署(?:记录)?|"
    r"(?:提交|撤销)报工(?:记录)?|(?:修改|更改|更新|删除|写入|下发|提交).{0,16}?"
    r"(?:工单|工序|作业卡|开工日期|开工时间|报工|检验记录|生产指令)|"
    r"(?:开始|启动|暂停|结束|完成|取消).{0,12}?(?:工序|作业卡)|"
    r"(?:登记|标记|设为|改为)(?:为)?(?:已经|已)?(?:开工|完工|完成|暂停|取消)"
)
_DIRECT = re.compile(
    r"^(?:那|那么)?(?:请|麻烦)?(?:你|您|系统|平台|MES|ERP)?(?:帮我|替我|为我)?"
    r"(?:在(?:MES|ERP|系统|平台)(?:里|中))?(?:直接|立即|马上|现在|实际|自动|执行|完成|进行|强制)*"
    r"(?:(?:把|将).{1,24})?$", re.I
)
_REQUEST = re.compile(r"帮我|替我|为我|强制")
_PLATFORM = re.compile(r"系统|平台|MES|ERP|助手|你", re.I)
_CAPABILITY = re.compile(
    r"(?:(?:系统|平台|MES|ERP|你)(?:是否)?(?:能否|可否|可以|能不能|会不会|能|能够)|"
    r"(?:能否|可否|可以|能不能|会不会|是否能|能|能够)(?:由|让)?(?:本)?(?:系统|平台|MES|ERP|你))"
    r"(?:直接|自动|实际|执行|完成|代为|来|帮我|替我|为我)*$", re.I
)
_INFORMATION = re.compile(r"说明|解释|介绍|了解|查询|查看|列出|判断|证明|代表|如何|怎么|怎样|什么条件|哪些条件")
_INFORMATION_TAIL = re.compile(
    r"^(?:(?:的|前|后|所需|需要|满足|需满足|要满足|哪些|什么|有哪些|有什么|有何)){0,4}"
    r"(?:要求|条件|流程|步骤|规则|说明|依据|记录|状态|权限|职责|含义|影响|区别|是什么|是什么意思|是否完成|完成了吗)"
)
_NEGATED = re.compile(
    r"(?:不要|不用|无需|别|不必|不要求|不需要|不是让|不能|不支持|不执行)"
    r"(?:你|您|系统|平台|MES|ERP|帮我|替我|为我|直接|自动|实际|执行|进行|完成|再|去|来|负责)*$", re.I
)


def requests_operation(question):
    """Recognize commands/capability questions, leaving knowledge queries alone.

    This does not grant tools or interpret arbitrary natural-language intent.
    The connector remains read-only regardless of the detected request type.
    """
    clauses = re.split(r"[，,。！？?!；;\n]+|(?:然后|并且|并)(?=请|帮我|替我|为我|直接|立即|实际|执行|让|由|在)", question)
    for clause in clauses:
        for action in _ACTION.finditer(clause):
            prefix, tail = clause[:action.start()].strip(), clause[action.end():].lstrip('的“”"「」 ')
            if _NEGATED.search(prefix) or _INFORMATION_TAIL.match(tail) or tail in ("前", "后"):
                continue
            if not prefix and not tail:
                continue  # A bare topic is not an explicit instruction.
            if _CAPABILITY.search(prefix):
                return True
            information = list(_INFORMATION.finditer(prefix))
            requests = list(_REQUEST.finditer(prefix))
            if information and (not requests or requests[-1].start() < information[-1].end()):
                continue
            if _DIRECT.fullmatch(prefix) or requests or _PLATFORM.search(prefix):
                return True
    return False
