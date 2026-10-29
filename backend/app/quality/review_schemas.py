"""Explicit human decisions; an issue confirmation alone does not approve a sample."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ReviewStatus = Literal["pending", "confirmed", "dismissed", "needs_evidence"]
RootCause = Literal["knowledge_missing", "outdated", "parsing", "retrieval", "rerank", "context",
                    "generation", "citation", "false_refusal", "ambiguous", "access_policy", "other"]
ScoringText = Annotated[str, Field(min_length=1, max_length=500)]


class FactAssertionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    polarity: Literal["positive", "negative"]
    contradictions: list[ScoringText] = Field(default_factory=list, max_length=32)
    use_literal: bool = True
    ignore_remediation_conditionals: bool = False


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    citation_id: str = Field(min_length=1, max_length=64)
    quote: str = Field(min_length=1, max_length=2000)

    @field_validator("citation_id", "quote")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("证据编号和摘录不能为空。")
        return value.strip()


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)
    review_status: ReviewStatus
    sample_status: Literal["draft", "approved"] = "draft"
    root_cause: RootCause | None = None
    review_reason: str = Field(min_length=1, max_length=2000)
    expected_answerable: bool | None = None
    expected_behavior: Literal["answer", "clarify", "refuse"] | None = None
    reference_answer: str | None = Field(default=None, max_length=8000)
    evaluation_question: str | None = Field(default=None, min_length=1, max_length=1000)
    required_facts: list[str] = Field(default_factory=list, max_length=32)
    fact_alternatives: dict[ScoringText, Annotated[list[ScoringText], Field(max_length=16)]] = Field(default_factory=dict, max_length=32)
    fact_assertions: dict[ScoringText, FactAssertionInput] = Field(default_factory=dict, max_length=32)
    accepted_behaviors: list[Literal["answer", "clarify", "refuse"]] = Field(default_factory=list, max_length=3)
    citation_requirement: Literal["none", "all_supporting_sources"] | None = None
    forbidden_claims: list[str] = Field(default_factory=list, max_length=32)
    evidence: list[EvidenceInput] = Field(default_factory=list, max_length=32)

    @field_validator("review_reason", "reference_answer", "evaluation_question")
    @classmethod
    def trim_text(cls, value):
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("审核说明和已填写的参考答案不能为空。")
        return value

    @field_validator("required_facts", "forbidden_claims")
    @classmethod
    def bounded_lines(cls, values):
        result = [value.strip() for value in values]
        if any(not value or len(value) > 500 for value in result) or len(set(result)) != len(result):
            raise ValueError("事实或禁止结论须为不重复的非空文本，每项最多500字符。")
        return result

    @model_validator(mode="after")
    def approval_contract(self):
        if (set(self.fact_alternatives) | set(self.fact_assertions)) - set(self.required_facts):
            raise ValueError("评分修订须对应当前必答事实。")
        if self.accepted_behaviors and (self.expected_behavior not in self.accepted_behaviors
                or len(set(self.accepted_behaviors)) != len(self.accepted_behaviors)
                or any((behavior == "answer") != self.expected_answerable for behavior in self.accepted_behaviors)):
            raise ValueError("允许的响应类型须与可回答性及预期行为一致。")
        if self.expected_behavior is not None and (self.expected_answerable is None or
                (self.expected_behavior == "answer") != self.expected_answerable):
            raise ValueError("预期行为与可回答性不一致；澄清和拒答均不可直接作答。")
        if self.review_status == "confirmed" and self.root_cause is None:
            raise ValueError("确认问题时必须选择根因。")
        if self.sample_status == "approved":
            if self.review_status != "confirmed" or self.expected_answerable is None or not self.reference_answer or not self.evaluation_question:
                raise ValueError("样本通过须先确认问题，并填写可独立理解的回归问题、预期可回答性和参考答案。")
            if self.expected_answerable and (not self.required_facts or not self.evidence):
                raise ValueError("可回答样本须有必答事实和来源证据。")
            if not self.expected_answerable and (self.required_facts or self.evidence):
                raise ValueError("资料不足样本不填写必答事实或支持答案的证据。")
        if len({item.citation_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("证据编号不可重复。")
        return self


class OpenCaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: UUID


class DatasetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    case_ids: list[UUID] = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, value):
        if not value.strip():
            raise ValueError("版本名称不能为空。")
        return value.strip()

    @field_validator("case_ids")
    @classmethod
    def no_duplicates(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("样本不可重复选择。")
        return values
