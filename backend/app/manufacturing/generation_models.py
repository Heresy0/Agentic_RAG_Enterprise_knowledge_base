"""Manufacturing-only generation schema; the ordinary schema stays unchanged."""
from typing import Literal
from pydantic import Field, field_validator, model_validator
from pydantic_core import PydanticCustomError
from ..knowledge.answer_models import AnswerClaim, ClaimKind, EvidenceFirstAnswerDraft
import json
import re


BusinessField = Literal[
    "work_order_id", "product_model", "operation_code", "actual_started_at", "actual_start_precision",
    "reported_at", "route_revision", "batch_no", "status", "plant_code", "change_notice_refs",
    "source_revision", "source_system_id", "source_record_id", "source_updated_at", "fetched_at",
]


class ManufacturingClaim(AnswerClaim):
    claim_kind: ClaimKind = Field(..., description="区分字段、适用性、一般要求、版本对比及记录。精确来源绑定后，用途由后端按字段、资料角色及已确认的版本比较模式确定。")
    business_field: BusinessField | None = Field(default=None, exclude_if=lambda value: value is None,
        description="选择一个工单字段名时，该字段是本条唯一事实来源，文字、值、角色及引用完全由后端生成；附带的text/citations/source_unit不参与取证。不能与calculation混用。")
    text: str = Field(default="", description="business_field、source_unit选择及applicability留空，由后端生成；仅旧格式填写完整原文结论。")
    citations: list[str] = Field(default_factory=list,
        description="business_field、source_unit选择及applicability均填写[]，引用由后端生成；仅旧格式填写支持原文的资料编号。")
    source_unit: str | None = Field(default=None, max_length=80, exclude_if=lambda value: value is None,
        description="优先选择上下文标记的完整原文条目，如资料1#2。仅选择标识，text留空、citations=[]、calculation=null；未使用时填null或空字符串。后端生成原文、引用及来源角色。")

    @field_validator("source_unit", mode="before")
    @classmethod
    def unused_source_unit(cls, value):
        # A blank optional selector makes no assertion and grants no source.
        # Required prose/field selection is still checked below.
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def field_selection(self):
        selected = self.source_unit is not None or self.business_field is not None
        if selected and self.calculation is not None:
            raise PydanticCustomError("manufacturing_selector_calculation", "来源选择不能与计算混用。")
        if self.business_field is None and self.source_unit is not None and not re.fullmatch(r"[^#\s]+#[1-9][0-9]*", self.source_unit):
            raise PydanticCustomError("manufacturing_source_unit_format", "原文条目标识须为上下文中的资料编号#条目序号；业务字段请使用business_field。")
        if not selected and not self.text.strip() and (self.claim_kind != "applicability" or self.calculation is not None):
            raise PydanticCustomError("manufacturing_empty_claim", "非字段结论必须有文字，只有后端适用性结论可留空。")
        return self


class ManufacturingGenerationDraft(EvidenceFirstAnswerDraft):
    claims: list[ManufacturingClaim] = Field(default_factory=list)


ExecutionBusinessField = Literal[BusinessField, 'operation_id', 'operation_actual_started_at', 'operation_start_precision',
    'operation_status', 'job_card_id', 'job_card_actual_started_at', 'job_card_start_precision', 'job_card_status', 'job_card_updated_at']


class ExecutionManufacturingClaim(ManufacturingClaim):
    business_field: ExecutionBusinessField | None = Field(default=None, exclude_if=lambda value: value is None,
        description=ManufacturingClaim.model_fields['business_field'].description)

    @model_validator(mode='after')
    def selected_field_exists(self):
        from .answer_context import current_manufacturing_context
        active = current_manufacturing_context()
        if active is not None and self.business_field is not None and self.business_field not in {row.field_name for row in active.items}:
            raise PydanticCustomError('manufacturing_execution_field_unavailable', '当前选择没有该业务字段来源。')
        return self


class ExecutionManufacturingGenerationDraft(ManufacturingGenerationDraft):
    claims: list[ExecutionManufacturingClaim] = Field(default_factory=list)


BatchBusinessField = Literal[BusinessField, 'work_order_number', 'batch_id', 'batch_number', 'batch_lot_number',
    'batch_status', 'batch_actual_started_at', 'batch_start_precision', 'batch_updated_at', 'step_id', 'step_number',
    'step_name', 'step_status', 'step_actual_started_at', 'step_start_precision', 'step_updated_at']


class BatchManufacturingClaim(ExecutionManufacturingClaim):
    business_field: BatchBusinessField | None = Field(default=None,exclude_if=lambda value:value is None,
        description=ManufacturingClaim.model_fields['business_field'].description)


class BatchManufacturingGenerationDraft(ManufacturingGenerationDraft):
    claims: list[BatchManufacturingClaim] = Field(default_factory=list)


def batch_start_projection(question, context, active):
    """Use verified fields for one simple entity-start question, including unknowns."""
    import re
    from ..knowledge.answer_coverage import requirement_prompt
    record=active.context.record
    if (record.schema_version!='openmes-work-order-v1' or not active.facts_only or
            not re.search(r'实际.*开工|开工时间|开工日期|何时开工|几点.*开工',question) or
            re.search(r'型号|状态|批号|编号|名称|路线|通知|来源|更新时间|采集|分别|比较|对比|先后|最早|以及|和|与',question)):
        return None
    mentioned=[prefix for prefix,pattern in [('step',r'步骤|工序'),('batch',r'批次'),('',r'工单')] if re.search(pattern,question)]
    if len(mentioned)>1:return None
    prefix=mentioned[0] if mentioned else 'step' if record.step is not None else 'batch' if record.batch is not None else ''
    requirements=json.loads(requirement_prompt(question,context))
    missing=prefix=='batch' and record.batch is None or prefix=='step' and record.step is None
    if missing:
        label='生产批次' if prefix=='batch' else '工序步骤'
        return BatchManufacturingGenerationDraft.model_validate(dict(answerable=False,response_type='clarify',missing_fields=[label],
            clarification_questions=['请先选择对应'+label+'。'],coverage=[dict(requirement_id=row['id'],aspect=row['question'],
                status='insufficient',claim_indices=[],missing_information='尚未选择'+label) for row in requirements]))
    names=[prefix+'_actual_started_at',prefix+'_start_precision'] if prefix else ['actual_started_at','actual_start_precision']
    return BatchManufacturingGenerationDraft.model_validate(dict(answerable=True,response_type='answer',
        claims=[dict(business_field=name,claim_kind='business_fact') for name in names],
        coverage=[dict(requirement_id=row['id'],aspect=row['question'],status='answered',claim_indices=[1,2],missing_information=None) for row in requirements]))


def execution_start_projection(question, context, active):
    """Project explicit ERP field lists or a simple start query from verified fields."""
    import re
    from ..knowledge.answer_coverage import requirement_prompt
    from .answer_context import requested_execution_fields
    execution = getattr(active.context.record, 'execution', None)
    if execution is None or not active.facts_only:
        return None
    names = list(requested_execution_fields(question))
    if not names:
        if (len({word for word in ('工单', '工序', '作业卡') if word in question}) > 1
                or not re.search(r'实际.*开工|开工时间|开工日期|何时开工|几点.*开工', question)
                or re.search(r'型号|批次|状态|路线|通知|来源|更新时间|采集|分别|比较|对比|先后|最早|以及|和|与', question)):
            return None
        if '作业卡' in question:
            prefix = 'job_card'
        elif '工序' in question:
            prefix = 'operation'
        elif '工单' in question:
            prefix = ''
        else:
            prefix = 'job_card' if execution.job_card_id else 'operation'
        names = [prefix + '_actual_started_at' if prefix else 'actual_started_at']
    for name in list(names):
        if name.endswith('actual_started_at'):
            names.append(name.replace('actual_started_at', 'start_precision') if name != 'actual_started_at' else 'actual_start_precision')
    names = list(dict.fromkeys(names))
    requirements = json.loads(requirement_prompt(question, context))
    if set(names) - {item.field_name for item in context.business_items}:
        return ExecutionManufacturingGenerationDraft.model_validate(dict(answerable=False, response_type='clarify',
            missing_fields=['作业卡'], clarification_questions=['请先选择所属工序下的具体作业卡。'],
            coverage=[dict(requirement_id=row['id'], aspect=row['question'], status='insufficient', claim_indices=[],
                missing_information='尚未选择具体作业卡') for row in requirements]))
    return ExecutionManufacturingGenerationDraft.model_validate(dict(answerable=True, response_type='answer',
        claims=[dict(business_field=name, claim_kind='business_fact') for name in names],
        coverage=[dict(requirement_id=row['id'], aspect=row['question'], status='answered', claim_indices=list(range(1, len(names)+1)), missing_information=None)
            for row in requirements]))


def precision_projection(question, context):
    """Known date + known precision answers a clock question without inventing a time."""
    from .answer_context import current_manufacturing_context, precision_fact_question
    from ..knowledge.answer_coverage import requirement_prompt
    active = current_manufacturing_context()
    if active is not None:
        projected = batch_start_projection(question, context, active)
        if projected is not None:
            return projected
        projected = execution_start_projection(question, context, active)
        if projected is not None:
            return projected
    if (active is None or not active.facts_only or active.context.record.actual_start_precision != "date"
            or not precision_fact_question(question)):
        return None
    items = [item for item in active.items if item.field_name in ("actual_started_at","actual_start_precision")]
    requirements = json.loads(requirement_prompt(question,context))
    return ManufacturingGenerationDraft.model_validate(dict(answerable=True,response_type="answer",
        claims=[dict(business_field=item.field_name,claim_kind="business_fact") for item in items],
        coverage=[dict(requirement_id=row["id"],aspect=row["question"],status="answered",claim_indices=[1,2],missing_information=None) for row in requirements]))


def applicability_claim(context):
    """The single source of the backend's verified version decision and bindings."""
    from .answer_context import current_manufacturing_context
    active = current_manufacturing_context()
    if active is None or active.facts_only or active.decision.state != 'applicable':
        return None
    required = {str(row.document_id) for row in active.decision.scope.manufacturing.documents
        if row.document_role == 'change_notice' or row.document_role == 'instruction'
        and row.business_revision == active.decision.selected_business_revision}
    if not required or not required <= {str(item.document_id) for item in context.items}:
        return None
    citations = [item.citation_id for item in context.items if str(item.document_id) in required]
    citations += [item.citation_id for item in active.items if item.field_name in active.required_fields]
    return ManufacturingClaim(text=active.applicability_text, citations=citations, claim_kind='applicability')


def compile_applicability_bindings(draft, context):
    """Compile the backend's exact selection as scoped answer context.

    Explicit selectors never use model-provided display text or references as
    facts. Legacy assertions still pass the per-claim source/numeric validator.
    """
    from .answer_context import current_manufacturing_context, complete_source_statement
    from .source_statements import source_units
    from ..evaluation.trace import record_rag_event
    active = current_manufacturing_context()
    if active is None or not draft.answerable or not draft.claims:
        return draft
    fields = {item.field_name: item for item in context.business_items}
    documents = {item.citation_id: item for item in context.items}
    units = source_units(context)
    def document_kind(item):
        if item.document_role in ('event_record', 'inspection_record'):
            return 'work_order_evidence'
        return 'version_comparison' if active.comparison and item.document_role == 'instruction' else 'work_requirement'
    field_bindings, rendered = [], []
    for claim in draft.claims:
        selected_unit = getattr(claim, "source_unit", None)
        field = getattr(claim, "business_field", None)
        ignored_display = [name for name in ("text", "citations") if getattr(claim, name)]
        if field is None and selected_unit in units:
            item, original = units[selected_unit]
            kind = document_kind(item)
            claim = claim.model_copy(update=dict(text=original, citations=[item.citation_id],
                claim_kind=kind, source_unit=None))
            field_bindings.append(dict(source_unit=selected_unit, compiled_citations=[item.citation_id],
                claim_kind=kind, ignored_display_fields=ignored_display, origin="server_source_unit"))
        elif field is None and selected_unit is not None:
            # An unresolved selector must never fall back to any accompanying
            # model assertion, including during final base-schema validation.
            claim = claim.model_copy(update=dict(text="", citations=[]))
        elif field is None and claim.calculation is None and len(claim.citations) == 1:
            # Legacy assertions can use the same source-owned role only when
            # their complete text already matches the exact cited source.
            key = claim.citations[0]
            item = next((item for item in context.business_items if item.citation_id == key), None)
            kind = None
            if item is not None and claim.text.rstrip('。') == item.content:
                kind = 'business_fact'
            elif (key in documents and documents[key].document_role != 'change_notice'
                  and complete_source_statement(claim.text, [documents[key]])):
                kind = document_kind(documents[key])
            if kind is not None and kind != claim.claim_kind:
                claim = claim.model_copy(update=dict(claim_kind=kind))
                field_bindings.append(dict(compiled_citations=[key], claim_kind=kind, origin='server_exact_source_role'))
        if field is not None:
            # The selected field must exist in this exact generation context.
            item = fields[field]
            claim = claim.model_copy(update=dict(text=item.content, citations=[item.citation_id],
                business_field=None, source_unit=None, claim_kind="business_fact"))
            field_bindings.append(dict(field=field, compiled_citations=[item.citation_id],
                ignored_display_fields=ignored_display, ignored_document_selector=selected_unit is not None,
                origin="server_business_field"))
        rendered.append(claim)
    if field_bindings:
        record_rag_event(dict(kind="manufacturing_binding_compilation", bindings=field_bindings, model_calls=0))
    if rendered != draft.claims:
        draft = draft.model_copy(update=dict(claims=rendered))
    applicability = applicability_claim(context)
    if applicability is None:
        return draft
    citations = applicability.citations
    changed=[];claims=[];indices={}
    work_order = next((item.citation_id for item in active.items if item.field_name == "work_order_id"), None)
    for index, claim in enumerate(draft.claims, 1):
        start = len(claims) + 1
        if getattr(claim, "source_unit", None) is not None:
            claims.append(claim)  # Unresolved selection stays invalid for every role hint.
            indices[index] = [start]
            continue
        if claim.claim_kind == "applicability" and not claim.text.strip() and claim.calculation is None:
            claim = claim.model_copy(update=dict(text=active.applicability_text))
            changed.append(dict(origin="server_applicability_selection", compiled_citations=citations))
        if (claim.claim_kind in (None, "applicability", "business_fact") and claim.text.rstrip("。") == active.applicability_text
                and claim.calculation is None and (claim.citations != citations or claim.claim_kind != "applicability")):
            changed.append(dict(original_citations=claim.citations,compiled_citations=citations))
            claim=claim.model_copy(update=dict(citations=list(citations),claim_kind="applicability"))
        parts = []
        if claim.claim_kind == "work_order_evidence" and claim.calculation is None and work_order:
            # Only compile complete, consecutive original sentences. A fragment,
            # paraphrase or other business field remains invalid for the validator.
            business_refs = set(claim.citations) & {item.citation_id for item in active.items}
            requested = [part.strip() for part in claim.text.split("。") if part.strip()]
            if business_refs <= {work_order} and requested:
                for item in context.items:
                    if item.citation_id not in claim.citations or item.document_role not in ("event_record", "inspection_record"):
                        continue
                    if complete_source_statement(claim.text, [item]):
                        parts = requested
                        break
        if parts:
            refs = list(dict.fromkeys([*claim.citations, work_order]))
            claims.extend(claim.model_copy(update=dict(text=part, citations=list(refs))) for part in parts)
            if len(parts) > 1 or refs != claim.citations:
                changed.append(dict(original_citations=claim.citations,compiled_citations=refs,origin="exact_record_sentences",sentence_count=len(parts)))
        else:
            claims.append(claim)
        indices[index] = list(range(start, len(claims)+1))
    if not active.comparison and not any(claim.claim_kind == "applicability" for claim in claims):
        # Selection is a backend fact. Work guidance consistently includes its
        # scope and both source types even if the model only states a rule.
        claims.append(applicability)
        changed.append(dict(original_citations=[],compiled_citations=citations,origin="server_applicability"))
    if changed:
        record_rag_event(dict(kind="manufacturing_binding_compilation",bindings=changed,model_calls=0))
        update = dict(claims=claims)
        if hasattr(draft, "coverage"):
            update["coverage"] = [row.model_copy(update=dict(claim_indices=[new for old in row.claim_indices for new in indices.get(old, [old])])) for row in draft.coverage]
        return draft.model_copy(update=update)
    return draft
