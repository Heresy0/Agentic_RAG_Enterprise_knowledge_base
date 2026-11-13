"""One live answer plan; source selections and prose compile to verified claims."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.json_schema import SkipJsonSchema

from ..knowledge.answer_models import AnswerClaim, EvidenceFirstAnswerDraft
from ..knowledge.prompts import ANSWER_SYSTEM_PROMPT
from .source_statements import source_units


class SourceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(description="选择当前上下文中的一个业务编号或完整原文条目标识。")


class SelectionDraft(EvidenceFirstAnswerDraft):
    model_config = ConfigDict(extra="forbid")
    claims: list[SourceSelection] = Field(default_factory=list)


class GroundedClaim(AnswerClaim):
    model_config = ConfigDict(extra="forbid")
    calculation: SkipJsonSchema[None] = Field(default=None, exclude_if=lambda value: value is None)


class GroundedDraft(EvidenceFirstAnswerDraft):
    claims: list[GroundedClaim] = Field(default_factory=list)


class LiveAnswerPlan(EvidenceFirstAnswerDraft):
    """Generation-only wire format; immediately compiled to GroundedDraft."""


def answer_sources(context, question=''):
    """Only current fields and conclusions already proved by existing backend code."""
    from .generation_models import applicability_claim
    from .source_statements import execution_boundary_source
    sources = {item.citation_id: AnswerClaim(text=item.content, citations=[item.citation_id],
                                           claim_kind='business_fact') for item in context.business_items}
    applicability = applicability_claim(context)
    if applicability is not None:
        sources['后端适用性'] = applicability
    boundary = execution_boundary_source(question, context)
    if boundary is not None:
        sources['后端开工边界'] = boundary
    return sources


def answer_schema(context, question=''):
    sources = tuple(answer_sources(context, question))
    fact = create_model('VerifiedAnswerSource', __base__=SourceSelection,
        source=(Literal[sources], Field(description='选择本次业务字段或后端核验结论编号；不另写文字或引用。')))
    documents = tuple(item.citation_id for item in context.items)
    claim = fact
    if documents:
        prose = create_model('DocumentAnswerClaim', __base__=GroundedClaim,
            citations=(list[Literal[documents]], Field(description='支持本条解释的文档编号；业务事实用source选择，不在文字中重写。')))
        claim = fact | prose
    return create_model('ManufacturingAnswerPlan', __base__=LiveAnswerPlan,
        claims=(list[claim], Field(default_factory=list)))


def lower_answer_plan(plan, context, question=''):
    # Recheck selectors against this exact request; no schema instance carries authority.
    selected = answer_schema(context, question).model_validate(plan.model_dump())
    sources = answer_sources(context, question)
    claims = [sources[row.source] if isinstance(row, SourceSelection) else row for row in selected.claims]
    return GroundedDraft.model_validate(selected.model_dump(exclude={'claims'}) | {
        'claims': [row.model_dump() for row in claims]})


def grounded_schema(context, *, claim=False):
    refs = tuple(dict.fromkeys(item.citation_id for item in [*context.items, *context.business_items]))
    model = create_model("ManufacturingGroundedClaim", __base__=GroundedClaim,
        citations=(list[Literal[refs]], Field(default_factory=list,
            description="支持本条文字的当前授权来源编号；直接选择编号，不添加方括号或正文。")))
    if claim:
        return model
    return create_model("ManufacturingGroundedDraft", __base__=GroundedDraft,
        claims=(list[model], Field(default_factory=list)))


def selection_schema(context):
    """Enumerate only sources already authorized for this exact request."""
    refs = tuple(dict.fromkeys([
        *(item.citation_id for item in context.business_items), *source_units(context),
    ]))
    if not refs:
        raise ValueError("制造来源选择需要已绑定的上下文")
    claim = create_model("ManufacturingSourceSelection", __base__=SourceSelection,
                         source=(Literal[refs], Field(description=SourceSelection.model_fields['source'].description)))
    return create_model("ManufacturingSelectionDraft", __base__=SelectionDraft,
                        claims=(list[claim], Field(default_factory=list,
                            description="仅选择回答问题所需来源；文字、角色、引用由后端生成。")))


def lower_selection(draft, context, legacy_schema):
    # Revalidate even a previously parsed model against the current request.
    # A stale or other-request selector must never reuse its old authorization.
    selected = selection_schema(context).model_validate(draft.model_dump())
    fields = {item.citation_id: item.field_name for item in context.business_items}
    claims = [dict(claim_kind="business_fact", business_field=fields[row.source])
              if row.source in fields else dict(claim_kind="work_requirement", source_unit=row.source)
              for row in selected.claims]
    return legacy_schema.model_validate(selected.model_dump(exclude={"claims"}) | {"claims": claims})


SELECTION_SYSTEM_PROMPT = ANSWER_SYSTEM_PROMPT + """
制造只读模式：
业务字段使用source选择对应业务编号，保留实体范围；不要自行重写值、状态含义或推测未知值。
询问当前选版及依据时选择后端适用性；若已提供后端开工边界，直接选择该结论，不再概括Completed含义。
文档作业要求、比较和解释使用text+citations；保留所问条件、例外和比较双方，不复述或推翻后端核验结论。
不能修改ERP/MES、代检验、代签署或代放行。
"""
