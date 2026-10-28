from copy import deepcopy
import hashlib

import pytest

from scripts.run_manufacturing_http_acceptance import main, snapshot_trace, usage_observation


def observation():
    case = dict(id="MFG-Q01", question="首件检查要求是什么？")
    response = dict(answer="检查后签署。", run_id="run-1")
    row = dict(knowledge_base_id="kb-1", document_name="指导书", content="检查后签署。",
               content_sha256=hashlib.sha256("检查后签署。".encode()).hexdigest())
    snapshot = dict(question=case["question"], retrieval_question=case["question"],
                    response=dict(answer=response["answer"]), snapshot_status="captured",
                    snapshot=dict(context=dict(items=[row], incomplete_reason=None), model_usage=[]),
                    config=dict(pipeline={}), request_id="request-1")
    return case, response, snapshot


def convert(case, response, snapshot):
    return snapshot_trace(case, response, snapshot, tenant_id="tenant-1", knowledge_base_ids={"kb-1"}, latency_ms=1)


def test_actual_observation_does_not_invent_unobserved_retrieval_stages():
    case, response, snapshot = observation()
    snapshot["snapshot"]["model_usage"] = [dict(stage="answer_generation", total_tokens=100)]
    trace = convert(case, response, snapshot)
    assert set(trace["stages"]) == {"context"}
    assert trace["snapshot_model_usage"] == [dict(stage="answer_generation", total_tokens=100)]
    assert trace["response"] == response and trace["scope"]["knowledge_base_ids"] == ["kb-1"]


@pytest.mark.parametrize("alteration", ["scope", "content", "answer", "question", "incomplete", "missing_capture"])
def test_snapshot_must_match_the_paid_request_and_authorized_evidence(alteration):
    case, response, original = observation()
    snapshot = deepcopy(original)
    if alteration == "scope":
        snapshot["snapshot"]["context"]["items"][0]["knowledge_base_id"] = "other-tenant-kb"
    elif alteration == "content":
        snapshot["snapshot"]["context"]["items"][0]["content"] = "已经篡改"
    elif alteration == "answer":
        snapshot["response"]["answer"] = "另一条回答"
    elif alteration == "question":
        snapshot["question"] = "另一道问题"
    elif alteration == "incomplete":
        snapshot["snapshot"]["context"]["incomplete_reason"] = "budget"
    else:
        snapshot["snapshot_status"] = "not_observed"
    with pytest.raises(ValueError):
        convert(case, response, snapshot)


def test_live_runner_requires_explicit_model_authorization_before_reading_files(tmp_path):
    with pytest.raises(SystemExit) as result:
        main(["--config", str(tmp_path / "absent.json"), "--tokens", str(tmp_path / "secret.json"),
              "--output-dir", str(tmp_path / "out")])
    assert result.value.code == 2
    assert not (tmp_path / "out").exists()


class LedgerClient:
    def __init__(self, payload):
        self.payload = payload

    def get(self, url, *, params, timeout):
        assert url.endswith("/kb-1/model-usage")
        assert params == dict(request_id="request-1", limit=100)
        return self

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_only_actual_ledger_ids_are_used_for_cost_deduplication():
    client = LedgerClient(dict(items=[dict(id="actual-event-id", request_id="request-1", total_tokens=100)], next_cursor=None))
    assert usage_observation(client, "http://127.0.0.1:8000", "kb-1", "request-1") == [
        dict(event_id="actual-event-id", request_id="request-1", total_tokens=100, persisted=True)]


@pytest.mark.parametrize("payload", [dict(items=[], next_cursor=dict(before_id="another-page")),
    dict(items=[dict(id="event-1", request_id="other-request")], next_cursor=None)])
def test_incomplete_or_unrelated_usage_cannot_be_reported_as_complete(payload):
    with pytest.raises(ValueError):
        usage_observation(LedgerClient(payload), "http://127.0.0.1:8000", "kb-1", "request-1")
