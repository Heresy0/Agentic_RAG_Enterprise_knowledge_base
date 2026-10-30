from copy import deepcopy
import base64
import hashlib
import json

import pytest

from scripts.run_manufacturing_http_acceptance import LocalKeycloakSession, main, snapshot_trace, usage_observation
from scripts import run_manufacturing_http_acceptance as runner


@pytest.mark.parametrize('port', [8000, 8001, 65535])
def test_local_candidate_port_is_allowed(port):
    assert runner.local_api_base(f'http://127.0.0.1:{port}/') == f'http://127.0.0.1:{port}'


@pytest.mark.parametrize('url', ['https://example.com:8000', 'http://0.0.0.0:8000',
    'http://user:password@127.0.0.1:8000', 'http://127.0.0.1:8000/path',
    'http://127.0.0.1:0', 'http://127.0.0.1:65536'])
def test_candidate_target_stays_on_explicit_loopback(url):
    with pytest.raises(ValueError):
        runner.local_api_base(url)


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


def jwt(exp):
    return "header." + base64.urlsafe_b64encode(json.dumps(dict(exp=exp)).encode()).decode().rstrip("=") + ".signature"


def test_local_token_refresh_precedes_qa_and_preserves_other_private_roles(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({"知识管理员": dict(access_token=jwt(1100), refresh_token="old-refresh"),
                                "操作员": dict(access_token="preserved")}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(runner.time, "time", lambda: 1000)
    requests_seen = []
    class TokenReply:
        def raise_for_status(self):
            pass
        def json(self):
            return dict(access_token=jwt(1300), refresh_token="new-refresh", expires_in=300)
    def renew(url, *, data, timeout):
        assert url == "http://127.0.0.1:8080/realms/enterprise-knowledge/protocol/openid-connect/token"
        assert data["grant_type"] == "refresh_token" and data["refresh_token"] == "old-refresh"
        requests_seen.append("refresh")
        return TokenReply()
    def paid(session, method, url, **kwargs):
        requests_seen.append("qa")
        assert session.headers["Authorization"] == "Bearer " + jwt(1300)
        return "observed-response"
    monkeypatch.setattr(runner.requests, "post", renew)
    monkeypatch.setattr(runner.requests.Session, "request", paid)
    client = LocalKeycloakSession(path)
    assert client.post("http://127.0.0.1:8000/api/qa", json={}) == "observed-response"
    assert requests_seen == ["refresh", "qa"] and client.auth_refreshes == 1
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["操作员"] == dict(access_token="preserved")
    assert saved["知识管理员"]["refresh_token"] == "new-refresh"


def test_failed_refresh_does_not_send_or_retry_qa(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({"知识管理员": dict(access_token=jwt(1100), refresh_token="old")}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(runner.time, "time", lambda: 1000)
    calls = []
    def fail(*args, **kwargs):
        calls.append("refresh")
        raise runner.requests.HTTPError("renewal failed")
    monkeypatch.setattr(runner.requests, "post", fail)
    monkeypatch.setattr(runner.requests.Session, "request", lambda *args, **kwargs: calls.append("qa"))
    with pytest.raises(runner.requests.HTTPError):
        LocalKeycloakSession(path).post("http://127.0.0.1:8000/api/qa", json={})
    assert calls == ["refresh"]
