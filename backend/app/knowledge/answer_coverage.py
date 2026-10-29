"""Low-cost question anchors and structural coverage, not a semantic judge.

Q anchors come only from the user question. E facets come from unambiguous,
authorized condition tables. No extra planning/retrieval/model call is made.
"""
import json
import re
from dataclasses import dataclass

from .answer_models import EvidenceCoverage, GenerationAnswerDraft
from .condition_requirements import condition_requirements


@dataclass(frozen=True)
class QuestionRequirement:
    id: str
    text: str
    citation_id: str | None = None


def question_requirements(question: str, context=None) -> list[QuestionRequirement]:
    # Conservative syntactic anchors, not domain-specific or semantic splitting.
    # Keep any long tail together rather than dropping parts of the user's query.
    parts = [part.strip() for part in re.findall(r"[^？?；;\n]+[？?；;]?", question) if part.strip()]
    anchored = []
    for part in parts:
        premise = re.match(r'^(?:均为|均按|全部为|时间(?:均)?为|时区(?:均)?为|币种(?:均)?为|单位(?:均)?为|假设|前提是)', part)
        asks = re.search(r'[？?]|什么|多少|多久|是否|如何|怎么|哪里|何时|请(?:说明|列出|计算)|解释|比较', part)
        if anchored and premise and not asks:
            anchored[-1] += ' ' + part
        else:
            anchored.append(part)
    parts = anchored
    if not parts and question.strip():
        parts = [question.strip()]
    if len(parts) > 8:
        parts = parts[:7] + ["\n".join(parts[7:])]
    anchors = [QuestionRequirement(f"Q{index}", text) for index, text in enumerate(parts, 1)]
    # A scoped input plan adds obligations; it never replaces original Q/E anchors.
    from .query_planner import current_query_plan
    plan = current_query_plan()
    planned = [QuestionRequirement(f"P{index}", row.question)
               for index, row in enumerate(plan.subquestions, 1)] if plan else []
    return anchors + planned + [QuestionRequirement(f"E{index}", row.text, row.citation_id)
                      for index, row in enumerate(condition_requirements(question, context), 1)]


def requirement_prompt(question: str, context=None) -> str:
    return json.dumps([dict(id=row.id, question=row.text,
                           **({'source': row.citation_id} if row.citation_id else {}))
                       for row in question_requirements(question, context)],
                      ensure_ascii=False)


def coverage_errors(draft: GenerationAnswerDraft, question: str, context=None) -> list[str]:
    requirements = {row.id: row for row in question_requirements(question, context)}
    expected = set(requirements)
    actual = {row.requirement_id for row in draft.coverage}
    errors = []
    if draft.answerable and draft.evidence_gap is not None:
        errors.append("可回答结果不能同时声明证据缺口")
    if expected - actual:
        errors.append("缺少必答问题的覆盖记录：" + "、".join(sorted(expected - actual)))
    if actual - expected:
        errors.append("包含不存在的必答问题编号：" + "、".join(sorted(actual - expected)))
    seen = set()
    for row in draft.coverage:
        key = (row.requirement_id, re.sub(r"\s+", "", row.aspect))
        if not key[1] or key in seen:
            errors.append("必答要点为空或重复：" + row.requirement_id)
        seen.add(key)
        indices = row.claim_indices
        if len(indices) != len(set(indices)) or any(index > len(draft.claims) for index in indices):
            errors.append("必答要点关联了重复或不存在的结论：" + row.requirement_id)
        if row.status == "answered":
            if not indices or not draft.answerable or row.missing_information is not None:
                errors.append("已回答要点缺少结论或与拒答状态矛盾：" + row.requirement_id)
            required = requirements.get(row.requirement_id)
            if required and required.citation_id and not any(
                    required.citation_id in draft.claims[index - 1].citations
                    for index in indices if 1 <= index <= len(draft.claims)):
                errors.append("条件要点缺少对应原文引用：" + row.requirement_id)
        elif indices or not (row.missing_information or "").strip():
            errors.append("未回答要点必须说明缺口且不能关联结论：" + row.requirement_id)
        if draft.answerable and row.status != "answered":
            errors.append("仍有未回答必答要点，不能标为完整可回答：" + row.requirement_id)
    return errors


def _quote_warnings(draft: GenerationAnswerDraft, context=None) -> list[str]:
    """Diagnostic metadata only; never reject claims or trigger model repair."""
    warnings = []
    for row in draft.coverage:
        if not isinstance(row, EvidenceCoverage):
            continue
        quote = re.sub(r'\s+', '', row.source_quote or '')
        if row.status == 'answered' and not quote:
            warnings.append('已回答要点缺少原文摘录：' + row.requirement_id)
        elif quote and context is not None:
            cited = {citation for index in row.claim_indices if 1 <= index <= len(draft.claims)
                     for citation in draft.claims[index - 1].citations}
            candidates = [item for item in context.items
                          if row.status != 'answered' or item.citation_id in cited]
            if not any(quote in re.sub(r'\s+', '', item.content) for item in candidates):
                warnings.append('原文摘录不在对应引用资料中：' + row.requirement_id)
    return warnings


def coverage_snapshot(draft: GenerationAnswerDraft, question: str, context=None) -> dict:
    errors = coverage_errors(draft, question, context)
    return dict(requirements=json.loads(requirement_prompt(question, context)),
                coverage=[row.model_dump() for row in draft.coverage], errors=errors,
                quote_warnings=_quote_warnings(draft, context),
                structurally_complete=draft.answerable and not errors,
                limitation="Quote diagnostics are non-blocking; required-point selection and semantic completeness still need independent review.")
