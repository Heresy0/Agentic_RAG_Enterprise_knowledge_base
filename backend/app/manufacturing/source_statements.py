"""Layout-aware original excerpts and bounded source-based comparisons.

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


def execution_boundary_question(question):
    """A direct inference from start registration to inspection/sign-off, not actual status."""
    lead = re.split(r'[？?]', question, maxsplit=1)[0]
    return bool(re.search(r'开工.{0,35}(?:能否|能不能|是否|可以|能).{0,12}(?:认定|证明|说明|意味着).{0,35}(?:首件|检验|质量签署|放行)', lead)
        and re.search(r'完成|已(?:经)?签署|已(?:经)?放行', lead))


def execution_boundary_source(question, context):
    """Reuse the existing proof on complete, currently bound source units."""
    from .answer_context import current_manufacturing_context
    from ..knowledge.answer_models import AnswerClaim, AnswerDraft
    active = current_manufacturing_context()
    if active is None or not execution_boundary_question(question):
        return None
    bound = {str(row.document_id) for row in active.decision.scope.manufacturing.documents}
    evidence = AnswerDraft(answerable=True, claims=[
        AnswerClaim(text=text, citations=[item.citation_id], claim_kind='work_requirement')
        for item in context.items if str(item.document_id) in bound
        for text in statement_units(item.content)])
    verdict = execution_boundary_verdict(question, evidence, context)
    return AnswerClaim(text=verdict['text'], citations=verdict['citations']) if verdict else None


def execution_boundary_verdict(question, draft, context):
    """Derive only the evidence boundary supported by explicit independent requirements."""
    from .answer_context import current_manufacturing_context, complete_source_statement, execution_question_text
    from .generation_selection import GroundedDraft
    active = current_manufacturing_context()
    if (not execution_boundary_question(question) or active is None or not draft.answerable
            or active.comparison or active.decision.state != 'applicable'):
        return None
    premise = execution_question_text(question).split('开工', 1)[0]
    names = {'作业卡': 'job_card_actual_started_at', '工序': 'operation_actual_started_at',
        '步骤': 'step_actual_started_at', '批次': 'batch_actual_started_at', '工单': 'actual_started_at'}
    mentioned = [name for name in names if name in premise]
    if len(mentioned) != 1:
        return None
    start = next((item for item in context.business_items if item.field_name == names[mentioned[0]]), None)
    if start is None:
        return None
    sources = {item.citation_id: item for item in context.items}
    for index, claim in enumerate(draft.claims):
        guides = [sources[key] for key in claim.citations if key in sources
            and sources[key].document_role in ('instruction', 'inspection', 'reference')
            and (sources[key].document_role != 'instruction' or sources[key].business_revision == active.decision.selected_business_revision)]
        if claim.calculation is not None:
            continue
        if isinstance(draft, GroundedDraft) and claim.claim_kind is None:
            # New prose does not carry a role or an exact excerpt. Derive the
            # same bounded verdict from complete units of its cited sources.
            statements = [(text, [guide]) for guide in guides for text in statement_units(guide.content)]
        elif claim.claim_kind == 'work_requirement' and complete_source_statement(claim.text, guides):
            statements = [(claim.text, guides)]
        else:
            continue
        for text, cited_guides in statements:
            # Supported rule forms explicitly require inspection AND quality sign-off.
            # Exceptions and other wording need a normal evidence-based answer.
            if re.search(r'除非|除外|无需|不必|免检|豁免', text):
                continue
            independent = re.search(r'(?:首件确认(?:须|必须)|每次开工连续检查)[^。；\n]*(?:所有必检项目全部合格|全部项目合格后)[，,]?(?:并)?由质量人员签署', text)
            # A complete, explicit AND-list is another independent-requirement form.
            # Merely finding inspection/signature words in adjacent prose is not proof.
            if (not independent and re.match(r'^恢复后续作业前必须同时满足以下条件[:：]\s*\n', text)
                    and re.search(r'(?m)^\s*[0-9]+[.、]\s*适用指导书要求的首件检查全部合格[。.]?\s*$', text)
                    and re.search(r'(?m)^\s*[0-9]+[.、]\s*质量人员完成签署(?:[，,][^。\n]*)?[。.]?\s*$', text)):
                independent = True
            direct = re.search(r'开工(?:登记|记录)(?:本身)?(?:不能|无法|不足以)(?:证明|认定)[^。；\n]*首件[^。；\n]*质量签署[^。；\n]*完成', text)
            if independent or direct:
                rendered = '仅凭实际开工登记，不能认定首件检验或质量签署已经完成，也不能据此判断实际是否完成'
                if independent:
                    rendered += '；仍需核验适用指导书要求的检验记录及质量签署'
                return dict(claim_index=index, text=rendered,
                    citations=list(dict.fromkeys([start.citation_id, *[item.citation_id for item in cited_guides]])))
    return None
