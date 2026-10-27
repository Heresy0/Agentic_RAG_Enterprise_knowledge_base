"""Use the real QA/feedback fixtures to exercise the complete human-review boundary."""
import hashlib
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from test_qa_feedback import data, ask, put
from backend.app.db.models import AuditEvent, KnowledgeBaseUserGrant
from backend.app.db.quality_models import QARun
from backend.app.db.review_models import QualityCase, QualityCaseRevision, QualityDatasetVersion
from backend.app.quality.reviews import ReviewStore
from scripts.evaluate_answers import load_dataset


def shared(data, *, export=True, all_scopes=False, **extra):
    data.current = data.principals[0]
    run_id = ask(data, all_scopes=all_scopes, **extra)
    response = put(data, run_id, rating="down", reason="incomplete", share_with_admins=True, allow_dataset_export=export)
    assert response.status_code == 200, response.text
    data.current = data.principals[3]
    return run_id


def opened(data, **kwargs):
    run_id = shared(data, **kwargs)
    response = data.client.post("/api/quality/cases", json={"run_id": run_id})
    assert response.status_code == 201, response.text
    return response.json()


def review_body(revision=1, **extra):
    return dict(expected_revision=revision, **{**dict(review_status="confirmed", sample_status="approved", root_cause="generation",
        evaluation_question="报销条件是什么？",
        review_reason="原证据包含例外条件，回答遗漏。", expected_answerable=True, reference_answer="应核对例外条件。",
        required_facts=["例外条件"], forbidden_claims=["无例外"], evidence=[dict(citation_id="1", quote="旧版例外条件")]), **extra})


def approve(data, case):
    response = data.client.put(f"/api/quality/cases/{case['case']['id']}", json=review_body(case["case"]["revision"]))
    assert response.status_code == 200, response.text
    return response.json()


def version(data, ids):
    response = data.client.post("/api/quality/datasets", json={"name": "内部回归v1", "case_ids": ids})
    assert response.status_code == 201, response.text
    return response.json()


def test_shared_inbox_includes_unopened_cases_and_creation_is_idempotent(data):
    run_id = shared(data)
    inbox = data.client.get("/api/quality/cases?review_status=pending")
    assert inbox.status_code == 200 and inbox.headers["cache-control"] == "no-store"
    assert len(inbox.json()["items"]) == 1 and inbox.json()["items"][0]["id"] is None
    first = data.client.post("/api/quality/cases", json={"run_id": run_id})
    second = data.client.post("/api/quality/cases", json={"run_id": run_id})
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["case"]["id"] == second.json()["case"]["id"]
    assert data.session.scalar(select(func.count()).select_from(QualityCaseRevision)) == 1


@pytest.mark.parametrize("who", ["owner", "peer", "partial_admin", "outside"])
def test_review_requires_shared_data_and_admin_on_all_scopes(data, who):
    case = opened(data, all_scopes=True)
    data.current = {"owner": data.principals[0], "peer": data.principals[1], "partial_admin": data.principals[2], "outside": data.outside}[who]
    assert data.client.get("/api/quality/cases").json()["items"] == []
    cid = case["case"]["id"]
    assert data.client.get(f"/api/quality/cases/{cid}").status_code == 404
    assert data.client.put(f"/api/quality/cases/{cid}", json=review_body()).status_code == 404
    assert data.client.post("/api/quality/cases", json={"run_id": case["case"]["run_id"]}).status_code == 404


def test_private_feedback_is_not_a_review_candidate(data):
    run_id = ask(data); put(data, run_id)
    data.current = data.principals[3]
    assert data.client.post("/api/quality/cases", json={"run_id": run_id}).status_code == 404


def test_reviews_append_history_and_prevent_lost_updates(data):
    case = opened(data); cid = case["case"]["id"]
    updated = approve(data, case)
    assert updated["case"]["revision"] == 2 and updated["case"]["export_eligible"]
    assert updated["history"][0]["review"]["reference_answer"] != case["source"]["response"]["answer"]
    assert updated["history"][1]["review"]["review_status"] == "pending"
    assert data.client.put(f"/api/quality/cases/{cid}", json=review_body()).status_code == 409
    assert data.session.scalar(select(func.count()).select_from(QualityCaseRevision)) == 2
    page = data.client.get(f"/api/quality/cases/{cid}?history_limit=1").json()
    assert page["next_history_before"] == 2
    older = data.client.get(f"/api/quality/cases/{cid}?history_before=2&history_limit=1").json()
    assert older["history"][0]["revision"] == 1


@pytest.mark.parametrize("invalid", [dict(root_cause=None), dict(review_reason=" "), dict(reference_answer=None),
    dict(required_facts=[]), dict(evidence=[]), dict(sample_status="approved", review_status="dismissed"),
    dict(required_facts=["same", "same"]), dict(root_cause="invented"), dict(reviewer_user_id=str(uuid4()))])
def test_sample_approval_rejects_missing_or_forged_criteria(data, invalid):
    cid = opened(data)["case"]["id"]
    assert data.client.put(f"/api/quality/cases/{cid}", json=review_body(**invalid)).status_code == 422
    assert data.session.get(QualityCase, UUID(cid)).revision == 1


def test_confirmed_issue_can_remain_a_draft_without_gold_answer(data):
    cid = opened(data)["case"]["id"]
    body = review_body(sample_status="draft", expected_answerable=None, reference_answer=None, required_facts=[], evidence=[])
    response = data.client.put(f"/api/quality/cases/{cid}", json=body)
    assert response.status_code == 200 and not response.json()["case"]["export_eligible"]


@pytest.mark.parametrize("evidence", [[dict(citation_id="1", quote="编造的原文")], [dict(citation_id="unknown", quote="旧版例外条件")]])
def test_evidence_must_be_exact_observed_source_text(data, evidence):
    cid = opened(data)["case"]["id"]
    assert data.client.put(f"/api/quality/cases/{cid}", json=review_body(evidence=evidence)).status_code == 409


def test_incomplete_snapshot_cannot_be_approved(data):
    case = opened(data)
    data.session.get(QARun, UUID(case["case"]["run_id"])).snapshot_status = "incomplete"; data.session.commit()
    assert data.client.put(f"/api/quality/cases/{case['case']['id']}", json=review_body()).status_code == 409


def test_export_is_versioned_loader_compatible_and_keeps_generated_answer_out_of_gold(data, tmp_path):
    case = approve(data, opened(data)); exported = version(data, [case["case"]["id"]]); did = exported["id"]
    response = data.client.get(f"/api/quality/datasets/{did}/samples")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert hashlib.sha256(response.content).hexdigest() == exported["manifest"]["samples_sha256"]
    assert data.client.get(f"/api/quality/datasets/{did}/samples").content == response.content
    path = tmp_path / "single_turn.jsonl"; path.write_bytes(response.content)
    sample = load_dataset(path)[0]
    assert sample["reference_answer"] == "应核对例外条件。" and sample["split"] == "regression"
    assert sample["question"] == "报销条件是什么？" and sample["provenance"]["observed_retrieval_question"] == "报销有什么条件？"
    assert "原始回答<script>" not in response.text
    assert sample["expected_evidence"][0]["text"] == "旧版例外条件"
    manifest = data.client.get(f"/api/quality/datasets/{did}/manifest").json()
    assert manifest["manifest"]["members"][0]["revision"] == 2


def test_export_needs_separate_owner_consent_and_reapproval_after_consent_change(data):
    case = approve(data, opened(data, export=False)); cid = case["case"]["id"]
    assert case["case"]["export_blocked_reason"] == "export_not_authorized"
    assert data.client.post("/api/quality/datasets", json=dict(name="v1", case_ids=[cid])).status_code == 409
    data.current = data.principals[0]
    put(data, case["case"]["run_id"], rating="down", reason="incomplete", share_with_admins=True, allow_dataset_export=True)
    data.current = data.principals[3]
    detail = data.client.get(f"/api/quality/cases/{cid}").json()
    assert detail["case"]["source_changed"] and not detail["case"]["export_eligible"]
    case = approve(data, detail)
    version(data, [cid])


@pytest.mark.parametrize("withdraw", ["export", "sharing", "admin", "document"])
def test_revocation_blocks_existing_export_and_hides_version_metadata(data, withdraw):
    case = approve(data, opened(data)); cid = case["case"]["id"]
    did = version(data, [cid])["id"]
    if withdraw in {"export", "sharing"}:
        data.current = data.principals[0]
        put(data, case["case"]["run_id"], rating="down", reason="incomplete", share_with_admins=withdraw != "sharing", allow_dataset_export=False)
        data.current = data.principals[3]
    elif withdraw == "admin":
        grant = data.session.get(KnowledgeBaseUserGrant, (data.current.tenant_id, data.bases[0].id, data.current.user_id))
        data.session.delete(grant); data.session.commit()
    else:
        data.session.delete(data.doc); data.session.commit()
    assert data.client.get(f"/api/quality/datasets/{did}/samples").status_code == 404
    assert data.client.get("/api/quality/datasets").json()["items"] == []


def test_corpus_or_review_changes_invalidate_frozen_export(data):
    case = approve(data, opened(data)); cid = case["case"]["id"]
    did = version(data, [cid])["id"]
    assert data.client.put(f"/api/quality/cases/{cid}", json=review_body(2, review_reason="重新确认说明")).status_code == 200
    assert data.client.get(f"/api/quality/datasets/{did}/samples").status_code == 409
    assert data.client.get("/api/quality/datasets").json()["items"][0]["current"] is False
    latest = data.client.get(f"/api/quality/cases/{cid}").json()
    second = version(data, [cid])["id"]
    data.doc.version += 1; data.session.commit()
    assert data.client.get(f"/api/quality/datasets/{second}/samples").status_code == 409
    assert data.client.get(f"/api/quality/cases/{cid}").json()["case"]["export_blocked_reason"] == "corpus_changed"
    assert data.client.put(f"/api/quality/cases/{cid}", json=review_body(latest["case"]["revision"])).status_code == 409


def test_review_and_revision_roll_back_with_audit(data, monkeypatch):
    run_id = shared(data)
    def fail(*_, **__): raise RuntimeError("audit failure")
    monkeypatch.setattr(ReviewStore, "audit", fail)
    with pytest.raises(RuntimeError, match="audit failure"):
        data.client.post("/api/quality/cases", json={"run_id": run_id})
    assert data.session.scalar(select(func.count()).select_from(QualityCase)) == 0
    assert data.session.scalar(select(func.count()).select_from(QualityCaseRevision)) == 0


def test_conversation_delete_cascades_reviews_and_invalidates_version(data):
    cid = data.client.post("/api/conversations").json()["id"]
    case = approve(data, opened(data, all_scopes=True, conversation_id=cid))
    did = version(data, [case["case"]["id"]])["id"]
    data.current = data.principals[0]
    assert data.client.delete(f"/api/conversations/{cid}").status_code == 204
    data.current = data.principals[3]
    for model in (QualityCase, QualityCaseRevision):
        assert data.session.scalar(select(func.count()).select_from(model)) == 0
    assert data.client.get(f"/api/quality/datasets/{did}/samples").status_code == 404


def test_rejected_answer_sample_remains_compatible_with_evaluation_loader(data, tmp_path):
    case = opened(data); cid = case["case"]["id"]
    response = data.client.put(f"/api/quality/cases/{cid}", json=review_body(expected_answerable=False,
        reference_answer="资料不足，无法确定。", required_facts=[], evidence=[]))
    assert response.status_code == 200, response.text
    did = version(data, [cid])["id"]
    content = data.client.get(f"/api/quality/datasets/{did}/samples").content
    path = tmp_path / "negative.jsonl"; path.write_bytes(content)
    assert load_dataset(path)[0]["answerable"] is False


def test_changed_corpus_can_be_recorded_as_a_draft_with_historical_evidence(data):
    case = approve(data, opened(data)); cid = case["case"]["id"]
    data.doc.version += 1; data.session.commit()
    response = data.client.put(f"/api/quality/cases/{cid}", json=review_body(2,
        sample_status="draft", review_status="needs_evidence", review_reason="语料已变化，等待新证据。"))
    assert response.status_code == 200 and response.json()["case"]["sample_status"] == "draft"


def test_same_conversation_samples_keep_one_group_even_when_questions_differ(data):
    cid = data.client.post("/api/conversations").json()["id"]
    first = approve(data, opened(data, all_scopes=True, conversation_id=cid))
    second = opened(data, all_scopes=True, conversation_id=cid, question="它还有例外吗？")
    second_id = second["case"]["id"]
    assert data.client.put(f"/api/quality/cases/{second_id}", json=review_body(evaluation_question="例外情况怎么处理？")).status_code == 200
    did = version(data, [first["case"]["id"], second_id])["id"]
    samples = [json.loads(line) for line in data.client.get(f"/api/quality/datasets/{did}/samples").text.splitlines()]
    assert len({sample["question"] for sample in samples}) == 2
    assert len({sample["group_key"] for sample in samples}) == 1


def test_mixed_scope_export_is_all_or_nothing(data):
    first = approve(data, opened(data))
    second = approve(data, opened(data, all_scopes=True))
    data.current = data.principals[2]
    response = data.client.post("/api/quality/datasets", json=dict(name="mixed", case_ids=[first["case"]["id"], second["case"]["id"]]))
    assert response.status_code == 404
    assert data.session.scalar(select(func.count()).select_from(QualityDatasetVersion)) == 0
