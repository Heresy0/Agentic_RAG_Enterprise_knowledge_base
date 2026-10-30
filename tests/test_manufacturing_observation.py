from uuid import uuid4
from langchain_core.documents import Document
from backend.app.quality.capture import SnapshotCapture
from backend.app.security.retrieval_scope import RetrievalScope
from backend.app.evaluation.metrics import score_answer
from backend.app.quality.review_schemas import ReviewInput
import pytest


def test_pipeline_is_bounded_scoped_and_contains_no_candidate_body():
    scope = RetrievalScope(uuid4(), knowledge_base_id=uuid4())
    capture = SnapshotCapture(scope=scope, retrieval_question="需要哪种扭矩规格？")
    doc = Document(page_content="尚未进入最终上下文的私有正文", metadata=dict(
        tenant_id=str(scope.tenant_id), knowledge_base_id=str(scope.knowledge_base_id),
        document_id=str(uuid4()), chunk_id="a"*64, parent_id="b"*64, document_version=2, rerank_score=0.8))
    capture.observe_stage("rerank", [doc])
    stage = capture.freeze()[1]["pipeline"]["stages"]["rerank"][0]
    assert stage["items"][0]["rerank_score"] == .8
    assert stage["items"][0]["parent_id"] == "b"*64 and stage["items"][0]["document_version"] == 2
    assert doc.page_content not in str(stage)
    foreign = Document(page_content="外部", metadata={**doc.metadata, "tenant_id": str(uuid4())})
    capture.observe_stage("vector", [foreign])
    assert capture.pipeline["vector"][0]["items"] == [] and capture.incomplete
    for _ in range(5): capture.observe_stage("rerank", [doc]*100)
    assert len(capture.pipeline["rerank"]) == 3
    assert not capture.freeze()[1]["pipeline"]["complete"]


def test_clarification_metric_requires_questions_not_just_false_answerability():
    case = dict(answerable=False, expected_behavior="clarify", required_facts=[], expected_evidence=[])
    refusal = dict(answerable=False, response_type="refuse", answer="资料不足", citations=[])
    assert not score_answer(case, refusal)["automatic_proxy_pass"]
    clarify = dict(refusal, response_type="clarify", missing_fields=["设备型号"],
                   clarification_questions=["请提供设备型号？"])
    assert score_answer(case, clarify)["automatic_proxy_pass"]
    clarify["clarification_questions"] = [" "]
    assert not score_answer(case, clarify)["automatic_proxy_pass"]


def test_review_can_explicitly_export_clarification_behavior():
    review = ReviewInput(expected_revision=1, review_status="confirmed", sample_status="approved",
        root_cause="ambiguous", review_reason="问题缺少设备型号", evaluation_question="这个设备要多久保养一次？",
        expected_answerable=False, expected_behavior="clarify", reference_answer="请提供设备型号。")
    assert review.expected_behavior == "clarify"
    with pytest.raises(ValueError):
        ReviewInput.model_validate({**review.model_dump(), "expected_answerable": True})
