"""Layout-aware original excerpts and one bounded duration comparison.

Excerpts remain source text. Lists keep their lead-in and all conditions; tables
keep their headers and complete rows. No semantic rewriting or external calls.
"""
from decimal import Decimal
import re


def statement_units(content):
    blocks, lines = [], []

    def flush():
        if lines:
            blocks.append("\n".join(lines).strip())
            lines.clear()

    for line in content.splitlines():
        if re.match(r"^\s*#{1,6}\s+", line):
            flush()
        elif not line.strip():
            flush()
        else:
            lines.append(line)
    flush()
    units = []
    for block in blocks:
        # A lead-in may be separated from its conditions by a blank line. Never
        # make that separation a license to quote only the consequence.
        if units and units[-1].rstrip().endswith(("：", ":", "，", ",", "；", ";")):
            units[-1] += "\n\n" + block
        else:
            units.append(block)
    return units


def complete_statement(text, items):
    target = re.sub(r"\s+", "", text).rstrip("。")
    if not target:
        return False
    for item in items:
        for unit in statement_units(item.content):
            source = re.sub(r"\s+", "", unit).rstrip("。")
            structured = "|" in unit or bool(re.search(r"(?m)^\s*(?:[-*+]\s*|\d+[.)、]\s*)", unit))
            if target == source or not structured and re.search(r"(?:^|。)" + re.escape(target) + r"(?:$|。)", source):
                return True
    return False


def source_units(context):
    return {f"{item.citation_id}#{index}": (item, text)
        for item in context.items for index, text in enumerate(statement_units(item.content), 1)}


def annotate_sources(context):
    rendered = context.text
    for item in context.items:
        units = statement_units(item.content)
        annotated = "\n".join(f"[原文条目 {item.citation_id}#{index}] {text[:160]}{'…（完整原文见下方内容）' if len(text) > 160 else ''}"
            for index, text in enumerate(units, 1))
        # ContextItem and its hash still contain the original content. Only the
        # model-facing view gains selectors; source metadata remains intact.
        rendered = rendered.replace("内容：" + item.content, "原文条目索引：\n" + annotated + "\n内容：" + item.content, 1)
    return rendered


_DURATION = r"(\d+(?:\.\d{1,6})?)(小时|分钟|秒)"


def duration_question(question):
    matches = list(re.finditer(r"连续停机(?:刚好|恰好|正好|为|了)\s*" + _DURATION, question))
    # Multiple or qualified scenarios need a normal evidence-based answer.
    if len(matches) != 1 or re.search(r"至少|最多|大约|约为|不到|不超过|不少于|可能|或者", question):
        return None
    match = matches[0]
    if re.search(r"不是|并非|没有|未曾", question[max(0, match.start() - 6):match.start()]):
        return None
    return match.groups()


def duration_verdict(question, claim, context):
    """Compare a stated hypothetical duration with an explicit OR-list trigger.

    This does not assert actual execution or decide whether production may resume.
    Unrecognized/ambiguous rules return no verdict, rather than guessing.
    """
    value = duration_question(question)
    if value is None or claim.claim_kind != "work_requirement" or claim.calculation is not None:
        return None
    guides = [item for item in context.items if item.citation_id in claim.citations
        and item.document_role == "instruction"]
    if not complete_statement(claim.text, guides):
        return None
    text = claim.text
    if (not re.match(r"^出现下列任一情形时必须重新进行首件确认[:：]\s*\n", text)
            or re.search(r"除外|除非|但是|无需|不必", text)
            or any(not re.match(r"\s*[-*+]", line) for line in text.splitlines()[1:] if line.strip())):
        return None
    triggers = re.findall(r"(?m)^\s*[-*+]\s*连续停机超过\s*" + _DURATION + r"后重新开工[。.]?\s*$", text)
    if len(triggers) != 1:
        return None
    limit = triggers[0]
    scale = {"小时": Decimal(3600), "分钟": Decimal(60), "秒": Decimal(1)}
    actual_seconds = Decimal(value[0]) * scale[value[1]]
    limit_seconds = Decimal(limit[0]) * scale[limit[1]]
    verdict = "已命中" if actual_seconds > limit_seconds else "未命中"
    return (f"问题假设连续停机{value[0]}{value[1]}，{verdict}“超过{limit[0]}{limit[1]}”这一重新首件触发条件；"
            "其他触发条件仍需分别核对，这不能证明实际复检已完成")
