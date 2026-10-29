from uuid import UUID
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CalculationOperand(BaseModel):
    source: str = Field(description="问题，或引用编号如资料1；输入必须来自该来源")
    quote: str = Field(min_length=1, max_length=300, description="含输入值和单位的连续原文摘录")
    value: str = Field(min_length=1, max_length=40, description="数值字符串或HH:MM:SS起始时刻")
    unit: Literal["时刻", "秒", "分钟", "小时", "元"]


class CalculationRequest(BaseModel):
    operation: Literal["time_add", "money_sum", "money_difference"]
    inputs: list[CalculationOperand] = Field(min_length=2, max_length=20)
    result: str | None = Field(default=None, min_length=1, max_length=50,
                               description="可留null。若填写，仅作审计用的模型猜测；最终结果由程序验证输入后计算")


class AnswerClaim(BaseModel):
    text: str = Field(
        min_length=1,
        description=(
        "依据知识库资料生成的一条独立结论，"
        "判定题先给总体结论，并引用适用规则与实际证据；不能只列事实。"
        "日期沿用所引原文，有年份就保留、无年份就不补充。"
        "文本中不要自行编造引用编号。"
        ),
    )
    citations: list[str] = Field(
        default_factory=list,
        description=(
            "支持该结论的资料编号数组。"
            "可回答时不能为空，例如："
            "['资料1'] 或 ['资料1', '资料2']。"
            "编号不能带方括号。"
        ),
    )
    calculation: CalculationRequest | None = Field(default=None,
        description="仅用于受限的时间累加或金额求和/差额；直接事实为null。需逐个提供输入来源。")


class AnswerDraft(BaseModel):
    answerable: bool
    claims: list[AnswerClaim] = Field(
        default_factory=list
    )
    refusal_reason: str | None = None


class AnswerCoverage(BaseModel):
    requirement_id: str = Field(min_length=1, max_length=20, description="必须使用问题清单中的编号，如Q1")
    source_quote: str | None = Field(default=None, exclude_if=lambda value: value is None)
    aspect: str = Field(min_length=1, max_length=200,
                        description="该问题实际要求回答的一个要点；同一问题可有多个要点，不是思维过程")
    status: Literal["answered", "insufficient", "withheld"] = Field(
        description="已回答、缺证据、因整体拒答未输出。问计划和是否已完成，且原文明示计划不能证明完成时，"
                    "有引用的计划与证据边界各标answered；问实际完成日期但无日期才标insufficient。不能把指路或背景当答案。")
    claim_indices: list[Annotated[int, Field(strict=True, ge=1)]] = Field(
        description="回答该要点的claims序号，从1开始；未回答时为空数组")
    missing_information: str | None = Field(
        description="未回答时明确缺口或整体拒答原因；已回答为null")


class EvidenceGap(BaseModel):
    """Visible retrieval feedback, never tool identity or a free-form query."""
    model_config = ConfigDict(extra="forbid")
    reason: Literal["missing_evidence", "conflict", "unsupported_request"]
    requirement_ids: list[str] = Field(min_length=1, max_length=3,
        description="本次清单中缺证据的必答项编号，最多三个，如Q1、P2")
    source_quote: str | None = Field(default=None, min_length=2, max_length=200,
        description="missing_evidence时填写原问题中需要重点查证的连续原文片段；其他原因为null。不是查询指令。")

    @field_validator("source_quote", mode="before")
    @classmethod
    def normalize_unused_quote(cls, value):
        return None if isinstance(value, str) and not value.strip() else value


class GenerationAnswerDraft(BaseModel):
    """Required live-generation contract; old stored drafts/public APIs stay unchanged."""

    coverage: list[AnswerCoverage] = Field(
        min_length=1, max_length=32,
        description="先识别各问题的必答要点，再生成对应结论；所有问题编号都须出现")
    answerable: bool = Field(description="本次原问题是否得到完整且有依据的回答。计划及原文明示的完成证据边界可完整回答计划/是否已完成问题，"
                            "但不能回答未知的实际完成日期。true时所有必答项answered；false时claims必须为空。")
    claims: list[AnswerClaim] = Field(default_factory=list)
    refusal_reason: str | None = None
    evidence_gap: EvidenceGap | None = Field(default=None,
        description="仅response_type=refuse且明确缺证据时请求一次定向补查；clarify或完整可回答必须为null，用户条件不能靠检索补齐。")
    response_type: Literal["answer", "clarify", "refuse"] = Field(
        description="必填，不得为null；明确区分有依据回答、需用户补充条件、资料缺失或超出能力拒答。")
    missing_fields: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list, max_length=5)
    clarification_questions: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(default_factory=list, max_length=3,
        description="clarify时填写直接向用户提出的简短追问；不含未经证实的业务结论或执行承诺。")


class EvidenceCoverage(AnswerCoverage):
    """Live evidence plan; historical coverage records retain their old schema."""
    aspect: str = Field(min_length=1, max_length=200,
                        description="A required answer point, not reasoning.")
    status: Literal["answered", "insufficient", "withheld"] = Field(
        description="Answered, missing evidence/user input, or withheld by overall refusal.")
    source_quote: str | None = Field(max_length=600,
        description="Verbatim evidence before aspect/claims; required for answered points, null for other statuses.")


class EvidenceFirstAnswerDraft(GenerationAnswerDraft):
    """Select evidence and its obligations within the existing generation call."""
    coverage: list[EvidenceCoverage] = Field(min_length=1, max_length=32,
        description="Quote relevant evidence, identify required points, then write claims. Cover every requirement.")


class Citation(BaseModel):
    citation_id: str
    document_id: UUID | None = None
    document_name: str
    section_path: str
    chunk_id: str | None = None
    parent_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    knowledge_base_id: UUID | None = None
    knowledge_base_name: str | None = None


class AnswerTimings(BaseModel):
    feedback_search_ms: float | None = Field(default=None, exclude_if=lambda value: value is None)
    answer_regeneration_ms: float | None = Field(default=None, exclude_if=lambda value: value is None)
    query_planning_ms: float | None = Field(default=None, exclude_if=lambda value: value is None)
    answer_repair_ms: float | None = Field(default=None, exclude_if=lambda value: value is None)
    agent_decision_ms: float | None = Field(default=None, exclude_if=lambda value: value is None)
    agent_tool_ms: float | None = Field(default=None, exclude_if=lambda value: value is None)
    hybrid_retrieval_ms: float | None = None
    rerank_ms: float | None = None
    search_total_ms: float
    context_build_ms: float
    generation_ms: float | None = None
    validation_ms: float | None = None
    render_ms: float | None = None
    total_ms: float


class AnswerResult(BaseModel):
    answer: str
    answerable: bool
    citations: list[Citation]
    refusal_reason: str | None = None
    timings: AnswerTimings | None = None
    response_type: Literal["answer", "clarify", "refuse"] | None = Field(default=None, exclude_if=lambda v: v is None)
    missing_fields: list[str] = Field(default_factory=list, exclude_if=lambda v: not v)
    clarification_questions: list[str] = Field(default_factory=list, exclude_if=lambda v: not v)
