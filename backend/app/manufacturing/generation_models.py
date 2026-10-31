"""Manufacturing-only generation schema; the ordinary schema stays unchanged."""
from pydantic import Field
from ..knowledge.answer_models import AnswerClaim, ClaimKind, EvidenceFirstAnswerDraft


class ManufacturingClaim(AnswerClaim):
    claim_kind: ClaimKind = Field(..., description="区分工单字段、适用判断、一般要求、版本对比及工单原文记录。")


class ManufacturingGenerationDraft(EvidenceFirstAnswerDraft):
    claims: list[ManufacturingClaim] = Field(default_factory=list)
