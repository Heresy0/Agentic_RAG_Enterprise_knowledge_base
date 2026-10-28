from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationInput(StrictInput):
    fixed_dataset_id: UUID
    feedback_dataset_id: UUID
    allow_model_calls: bool = False
    max_cases: int = Field(default=40, ge=2, le=200, strict=True)
    max_model_calls: int = Field(default=200, ge=1, le=2000, strict=True)
    max_tokens: int = Field(default=1_000_000, ge=1000, le=10_000_000, strict=True)
    reservation_tokens: int = Field(default=16000, ge=1000, le=65536, strict=True)
    max_seconds: int = Field(default=600, ge=10, le=3600, strict=True)

    @model_validator(mode="after")
    def explicit_calls(self):
        if not self.allow_model_calls:
            raise ValueError("创建评测任务须明确允许模型调用。")
        if self.fixed_dataset_id == self.feedback_dataset_id:
            raise ValueError("固定集与反馈回归集必须是两份不同版本。")
        if self.reservation_tokens > self.max_tokens:
            raise ValueError("单次调用预留量不能超过任务 Token 预算。")
        return self


class EvaluationReviewInput(StrictInput):
    response_binding: str = Field(pattern=r"^[0-9a-f]{64}$")
    verdict: Literal["passed", "failed", "uncertain"]
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError("复核说明不能为空。")
        return value.strip()


class ImprovementInput(StrictInput):
    case_id: UUID
    regression_case_id: UUID | None = None
    owner_user_id: UUID | None = None
    description: str = Field(min_length=1, max_length=4000)
    change_ref: str = Field(min_length=1, max_length=128)
    max_latency_ratio: float = Field(default=1.5, ge=1, le=5, allow_inf_nan=False)
    max_token_ratio: float = Field(default=1.3, ge=1, le=5, allow_inf_nan=False)

    @field_validator("description", "change_ref")
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError("改进说明与变更引用不能为空。")
        return value.strip()


class ImprovementUpdate(ImprovementInput):
    expected_revision: int = Field(ge=1, strict=True)


class VerificationInput(StrictInput):
    expected_revision: int = Field(ge=1, strict=True)
    baseline_job_id: UUID
    candidate_job_id: UUID
    note: str = Field(min_length=1, max_length=2000)
    acknowledge_unknown_usage: bool = False

    @field_validator("note")
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError("发布检查说明不能为空。")
        return value.strip()


class CloseInput(StrictInput):
    expected_revision: int = Field(ge=1, strict=True)
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("note")
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError("关闭说明不能为空。")
        return value.strip()
