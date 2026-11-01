from __future__ import annotations

import re
import unicodedata

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .answer_models import AnswerDraft, GenerationAnswerDraft
from .answer_coverage import coverage_errors
from .context_builder import BuiltContext
from .calculation_service import CalculationError, compute
from ..documents.policy_metadata import policy_label
from .temporal import expand_omitted_years


FACT_PATTERN = re.compile(
    r"""
    (?P<date>
        (?<!\d)
        \d{4}
        (?:
            年\d{1,2}月\d{1,2}日
            |
            [-/]\d{1,2}[-/]\d{1,2}
        )
        (?!\d)
    )
    |
    (?P<percent>
        (?<![\d.])
        -?\d+(?:,\d{3})*(?:\.\d+)?
        %
    )
    |
    (?P<number>
        (?<![\d.])
        -?\d+(?:,\d{3})*(?:\.\d+)?
        (?![\d.%])
    )
    """,
    flags=re.VERBOSE,
)


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(
        default_factory=list
    )
    coverage_errors: list[str] = field(default_factory=list)


def interaction_errors(draft):
    errors = []
    if not isinstance(draft, GenerationAnswerDraft):
        return errors
    if draft.response_type == "clarify":
        if draft.answerable or draft.claims or draft.evidence_gap or not draft.missing_fields or not draft.clarification_questions:
            errors.append("澄清须说明缺失条件并直接追问，且不能包含已回答结论或补查动作")
        elif any(not value.strip() for value in draft.missing_fields + draft.clarification_questions):
            errors.append("澄清条件及问题不能为空白")
    elif draft.missing_fields or draft.clarification_questions:
        errors.append("非澄清结果不应携带待补条件或追问")
    if draft.response_type == "answer" and not draft.answerable or draft.response_type == "refuse" and draft.answerable:
        errors.append("回答类型与可回答性矛盾")
    return errors


class AnswerValidator:
    @classmethod
    def _manufacturing_quantities(cls, text):
        """Keep units bound to quantities; revision 2.0 cannot prove two pieces."""
        digits = {char: index for index, char in enumerate("零一二三四五六七八九")}
        digits["两"] = 2
        quantities = set()
        for match in re.finditer(r"(?<![\d.零一二两三四五六七八九十百千])([0-9]+(?:\.[0-9]+)?|[零一二两三四五六七八九十百千]+)\s*(件|次)", text):
            value, unit = match.groups()
            if value[0].isdigit():
                number = cls._normalize_decimal(value)
            else:
                total = current = 0
                for char in value:
                    if char in digits: current = digits[char]
                    else:
                        total += (current or 1) * {"十": 10, "百": 100, "千": 1000}[char]
                        current = 0
                number = str(total + current)
            quantities.add((unit, number))
        return quantities

    def validate(
        self,
        *,
        draft: AnswerDraft | GenerationAnswerDraft,
        context: BuiltContext,
        question: str = "",
    ) -> ValidationResult:
        errors = interaction_errors(draft)
        coverage_failures = (coverage_errors(draft, question, context)
                             if isinstance(draft, GenerationAnswerDraft) else [])
        errors.extend(coverage_failures)

        all_items = [*context.items, *context.business_items]
        allowed_ids = {
            item.citation_id
            for item in all_items
        }
        item_map = {
            item.citation_id: item
            for item in all_items
        }
        question_facts = self._extract_numeric_facts(
            expand_omitted_years(question)
        )

        if not draft.answerable:
            if draft.claims:
                errors.append(
                    "拒答结果不应同时包含确定性结论"
                )

            return ValidationResult(
                valid=not errors,
                errors=errors,
                coverage_errors=coverage_failures,
            )

        if not draft.claims:
            errors.append(
                "可回答结果至少需要一个结论"
            )

        for claim_index, claim in enumerate(
            draft.claims,
            start=1,
        ):
            if not claim.citations:
                errors.append(
                    f"第{claim_index}个结论没有引用"
                )
                continue

            invalid_ids = [
                citation_id
                for citation_id in claim.citations
                if citation_id not in allowed_ids
            ]

            if invalid_ids:
                errors.append(
                    f"第{claim_index}个结论包含"
                    f"不存在的引用：{invalid_ids}"
                )
                continue

            if context.business_items:
                from ..manufacturing.answer_context import current_manufacturing_context
                active = current_manufacturing_context()
                business = {item.citation_id: item for item in context.business_items}
                document_ids = {item.citation_id for item in context.items}
                cited_business = [business[key] for key in claim.citations if key in business]
                cited_documents = [item_map[key] for key in claim.citations if key in document_ids]
                if active is not None and active.facts_only and claim.claim_kind != "business_fact":
                    errors.append(f"第{claim_index}个工单字段查询不能用文档条件代替业务事实")
                    continue
                if claim.claim_kind == "business_fact":
                    if (len(claim.citations) != 1 or len(cited_business) != 1
                            or claim.text.rstrip("。") != cited_business[0].content or claim.calculation is not None):
                        errors.append(f"第{claim_index}个工单事实必须逐字对应单个业务字段")
                    continue
                if claim.claim_kind == "applicability":
                    required_docs = {str(row.document_id) for row in active.decision.scope.manufacturing.documents
                        if row.document_role == "change_notice" or row.document_role == "instruction"
                        and row.business_revision == active.decision.selected_business_revision} if active else set()
                    if (active is None or active.decision.state != "applicable" or active.facts_only
                            or claim.text.rstrip("。") != active.applicability_text
                            or not active.required_fields <= {item.field_name for item in cited_business}
                            or not required_docs <= {str(item.document_id) for item in cited_documents}
                            or not cited_documents or claim.calculation is not None):
                        errors.append(f"第{claim_index}个选版结论缺少后端结论或两类来源绑定")
                    continue
                if claim.claim_kind == "work_order_evidence":
                    events = [item for item in cited_documents if item.document_role in ("event_record", "inspection_record")]
                    sentences = {sentence.strip().strip("。！？；; \n") for item in events
                        for sentence in re.split(r"[。！？；;\n]+", item.content)}
                    if (not events or len(cited_business) != 1 or cited_business[0].field_name != "work_order_id"
                            or claim.text.strip().strip("。！？；; \n") not in sentences or claim.calculation is not None):
                        errors.append(f"第{claim_index}个工单记录结论须保留完整原句并绑定工单编号")
                    continue
                comparing = claim.claim_kind == "version_comparison"
                guides = [item for item in cited_documents if item.document_role == "instruction"]
                if comparing and (active is None or not active.comparison or not guides
                        or len({item.business_revision for item in guides}) != 1
                        or any(item.business_revision not in claim.text for item in guides)):
                    errors.append(f"第{claim_index}个版本对比未明确绑定单个业务修订")
                    continue
                if (claim.claim_kind not in ("work_requirement", "version_comparison") or not cited_documents or cited_business
                        or re.search(r"该工单|本工单|此工单|这张工单|WO-[A-Za-z0-9-]+", claim.text)):
                    errors.append(f"第{claim_index}个作业要求必须以文档为据，工单适用结论需单独绑定")
                    continue
                if not comparing and (not any(item.document_role in ("instruction", "inspection", "change_notice", "reference", "route") for item in cited_documents)
                        or re.search(r"系统.*(?:已|完成|成功).*(?:放行|执行|修改|提交|下发)|已(?:放行|下发)|已恢复(?:生产|作业)", claim.text)):
                    errors.append(f"第{claim_index}个作业要求不能冒充执行记录或系统操作结果")
                    continue
                if not comparing and active is not None and any(item.business_revision != active.decision.selected_business_revision for item in guides):
                    errors.append(f"第{claim_index}个作业要求引用了仅供对比的非适用指导书")
                    continue
                quantity_sources = guides if guides and re.search(r"首件|连续.*件", claim.text) else cited_documents
                if (not comparing and self._manufacturing_quantities(claim.text)
                        and re.search(r"首件|连续.*件", claim.text) and not guides):
                    errors.append(f"第{claim_index}个首件数量须引用当前适用指导书正文，不能仅引用并列新旧数量的通知")
                    continue
                missing_quantities = self._manufacturing_quantities(claim.text) - self._manufacturing_quantities(
                    "\n".join(item.content for item in quantity_sources))
                if missing_quantities:
                    errors.append(f"第{claim_index}个结论中的件数或次数缺少对应文档数量支持")
                # Business IDs and question numbers cannot legitimize process limits.
                question_facts = set()

            if claim.calculation is not None:
                try:
                    calculation = compute(claim, context, question)
                    if claim.text != calculation.rendered_text:
                        errors.append(f"第{claim_index}个计算结论不是程序生成的受控表达")
                except CalculationError as exc:
                    errors.append(f"第{claim_index}个计算结论校验失败：{exc}")
                # Only program-rendered calculation prose is exempt from literal
                # number matching. Direct claims still take the original path.
                continue

            cited_text = "\n".join(
                item_map[citation_id].content + "\n" + policy_label(item_map[citation_id].policy)
                for citation_id in claim.citations
            )

            claim_facts = self._extract_numeric_facts(
                claim.text
            )
            evidence_facts = self._extract_numeric_facts(
                cited_text
            )
            if context.business_items:
                evidence_facts |= {"number:" + number for _, number in self._manufacturing_quantities(cited_text)}

            unsupported_facts = sorted(
                claim_facts
                - evidence_facts
                - question_facts
            )

            if unsupported_facts:
                readable_facts = [
                    self._display_fact(fact)
                    for fact in unsupported_facts
                ]

                errors.append(
                    f"第{claim_index}个结论中的数字或日期"
                    f"缺少证据支持：{readable_facts}"
                )

        if context.business_items:
            from ..manufacturing.answer_context import current_manufacturing_context
            active = current_manufacturing_context()
            if active is not None and active.comparison and draft.answerable:
                revisions = {item_map[key].business_revision for claim in draft.claims
                    if claim.claim_kind == "version_comparison" for key in claim.citations
                    if key in item_map and getattr(item_map[key], "document_role", None) == "instruction"}
                if len(revisions) < 2:
                    errors.append("版本比较必须分别引用至少两个业务修订")
        return ValidationResult(
            valid=not errors,
            errors=errors,
            coverage_errors=coverage_failures,
        )

    @classmethod
    def _extract_numeric_facts(
        cls,
        text: str,
    ) -> set[str]:
        # 将全角数字、全角百分号等转换为半角形式
        normalized_text = unicodedata.normalize(
            "NFKC",
            text,
        )

        facts: set[str] = set()

        for match in FACT_PATTERN.finditer(
            normalized_text
        ):
            fact_type = match.lastgroup

            if fact_type is None:
                continue

            raw_value = match.group(fact_type)

            if fact_type == "date":
                facts.add(
                    cls._normalize_date(raw_value)
                )
            elif fact_type == "percent":
                number = raw_value.removesuffix("%")
                facts.add(
                    "percent:"
                    + cls._normalize_decimal(number)
                )
            else:
                facts.add(
                    "number:"
                    + cls._normalize_decimal(raw_value)
                )

        return facts

    @staticmethod
    def _normalize_decimal(value: str) -> str:
        decimal_value = Decimal(
            value.replace(",", "")
        )

        normalized = format(
            decimal_value.normalize(),
            "f",
        )

        # Decimal("-0") 统一成 "0"
        if decimal_value == 0:
            return "0"

        return normalized

    @staticmethod
    def _normalize_date(value: str) -> str:
        normalized = (
            value.replace("年", "-")
            .replace("月", "-")
            .replace("日", "")
            .replace("/", "-")
        )

        year, month, day = normalized.split("-")

        try:
            # 同时检查 2 月 30 日等非法日期。
            parsed = date(
                int(year),
                int(month),
                int(day),
            )
        except ValueError:
            # 模型生成非法日期时不能让问答接口崩溃。
            # 保留标准化原值，使其只能与证据中的
            # 同一个非法日期匹配。
            return f"invalid-date:{normalized}"

        return f"date:{parsed.isoformat()}"

    @staticmethod
    def _display_fact(fact: str) -> str:
        fact_type, value = fact.split(":", 1)

        if fact_type == "percent":
            return f"{value}%"

        if fact_type == "date":
            return value

        return value
