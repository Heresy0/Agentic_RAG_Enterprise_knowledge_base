"""Run a selected frozen manufacturing split through authenticated HTTP endpoints.

Snapshots are read through the same owner's API. No QA-request retries, account
creation or source/criteria edits. Raw authorized context stays in local output.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.evaluate_answers import load_dataset


class SourceUnavailable(ValueError):
    """A frozen case remains in the denominator but sends no paid QA request."""



class LocalKeycloakSession(requests.Session):
    """Renew a short local demo token before requests; never retry paid QA."""
    def __init__(self, tokens_path):
        super().__init__()
        self.tokens_path = tokens_path
        self.auth_refreshes = 0
        self._load()

    def _load(self):
        self.private_tokens = json.loads(self.tokens_path.read_text(encoding="utf-8-sig"))
        self.admin_tokens = self.private_tokens["知识管理员"]
        token = self.admin_tokens["access_token"]
        # Unverified exp is used only for renewal scheduling. The API still
        # verifies the token's signature, issuer, audience and authorization.
        self.expires_at = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))["exp"]
        self.headers["Authorization"] = "Bearer " + token

    def request(self, method, url, **kwargs):
        if self.expires_at - time.time() < 240:
            refresh = self.admin_tokens.get("refresh_token")
            if not refresh:
                raise ValueError("Local token renewal requires an existing refresh token")
            response = requests.post("http://127.0.0.1:8080/realms/enterprise-knowledge/protocol/openid-connect/token",
                                     data=dict(grant_type="refresh_token", client_id="enterprise-knowledge-cli", refresh_token=refresh), timeout=20)
            response.raise_for_status()
            payload = response.json()
            if not payload.get("access_token"):
                raise ValueError("Keycloak did not return an access token")
            self.private_tokens["知识管理员"].update({key: payload[key] for key in
                ("access_token", "refresh_token", "expires_in", "refresh_expires_in") if key in payload})
            pending = self.tokens_path.with_suffix(self.tokens_path.suffix + ".pending")
            pending.write_text(json.dumps(self.private_tokens, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            pending.replace(self.tokens_path)
            self._load()
            if self.expires_at - time.time() < 240:
                raise ValueError("Renewed local token lifetime is too short for the bounded request")
            self.auth_refreshes += 1
        return super().request(method, url, **kwargs)


def select_cases(dataset_dir, *, split=None, limit=None, case_ids=None, max_questions=None):
    """Resolve and bound the paid workload before reading tokens or using HTTP."""
    if (dataset_dir / "splits.json").exists():
        from scripts.validate_manufacturing_public import validate_freeze
        if split not in ("development", "test") or max_questions is None:
            raise ValueError("Frozen corpora require an explicit --split and --max-questions budget")
        edition = json.loads((dataset_dir / "splits.json").read_text(encoding="utf-8")).get("edition")
        if edition == "manufacturing-zh-v3":
            from scripts.validate_manufacturing_zh import validate_freeze as validate_zh
            frozen, _, _ = validate_zh(dataset_dir, ROOT / "sample_docs/manufacturing_zh_v3")
            cases = load_dataset(dataset_dir / split / "single_turn.jsonl")
        else:
            frozen, splits, _ = validate_freeze(dataset_dir, ROOT / "sample_docs/manufacturing_public_v2")
            cases = splits[split]
        dataset = dataset_dir / split / "single_turn.jsonl"
    else:
        if split is not None:
            raise ValueError("The baseline does not have development/test split directories")
        dataset = dataset_dir / "single_turn.jsonl"
        cases = load_dataset(dataset)
        if len(cases) != 20 or {row["id"] for row in cases} != {f"MFG-Q{i:02}" for i in range(1, 21)}:
            raise ValueError("Expected the frozen 20-question manufacturing baseline")
        frozen = None
    if case_ids:
        if len(set(case_ids)) != len(case_ids) or not set(case_ids) <= {row["id"] for row in cases}:
            raise ValueError("Case IDs must be unique and belong to the selected split")
        cases = [row for row in cases if row["id"] in set(case_ids)]
    if limit is not None:
        if limit < 1:
            raise ValueError("Question limit must be positive")
        cases = cases[:limit]
    if max_questions is not None and (not 1 <= max_questions <= 150 or len(cases) > max_questions):
        raise ValueError("Selected workload exceeds the explicit question budget")
    return dataset, cases, frozen


def check_v2_inventory(http, base, kb_map, frozen, config):
    if config.get("corpus_manifest_sha256") != frozen["corpus_manifest_sha256"]:
        raise ValueError("Runtime config is not bound to the frozen V2 corpus")
    manifest = json.loads((ROOT / "sample_docs/manufacturing_public_v2/manifest.json").read_text(encoding="utf-8"))
    for group, kb in kb_map.items():
        response = http.get(base + "/api/knowledge-bases/" + kb + "/documents", timeout=20)
        response.raise_for_status()
        rows = response.json()["items"]
        expected = {Path(row["file"]).name for row in manifest["documents"] if row["group"] == group}
        if len(rows) != len(expected) or {r["file_name"] for r in rows} != expected or any(r["status"] != "ready" for r in rows):
            raise ValueError("V2 corpus is not fully ready; refuse paid requests against a partial library")


def check_zh_inventory(http, base, kb_map, frozen, config, cases, *, record_unavailable=False):
    """Require every selected case's source; keep unrelated failed scans visible.

    The bounded development run may proceed with a failed test-only source.
    By default an unavailable required source blocks the entire run. An explicit
    accounting mode records affected cases without sending those QA requests.
    SQL/hash/vector/version verification is separately bound by an audit file.
    """
    if config.get("corpus_manifest_sha256") != frozen["corpus_manifest_sha256"]:
        raise ValueError("Runtime config is not bound to the frozen Chinese corpus")
    from backend.app.knowledge.rag import CHUNKING_VERSION
    from scripts.validate_manufacturing_public import parser_fingerprint
    audit_path = Path(config["index_audit_file"])
    if hashlib.sha256(audit_path.read_bytes()).hexdigest() != config.get("index_audit_sha256"):
        raise ValueError("Index audit fingerprint differs")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if (audit["tenant_id"] != config["tenant_id"] or audit["chunking_version"] != CHUNKING_VERSION
            or audit["parser_sha256"] != parser_fingerprint() or not audit["all_checks_passed"]):
        raise ValueError("Chinese index audit scope, parser or verification differs")
    manifest = json.loads((ROOT / "sample_docs/manufacturing_zh_v3/manifest.json").read_text(encoding="utf-8"))
    expected = {Path(r["file"]).name:r for r in manifest["documents"]}
    actual = {}
    for group, kb in kb_map.items():
        response = http.get(base + "/api/knowledge-bases/" + kb + "/documents", timeout=20)
        response.raise_for_status()
        rows = response.json()["items"]
        names = {Path(r["file"]).name for r in manifest["documents"] if r["group"] == group}
        if len(rows) != len(names) or {r["file_name"] for r in rows} != names:
            raise ValueError("Chinese corpus inventory differs")
        actual.update({r["file_name"]:r for r in rows})
    audited = {r["file_name"]:r for r in audit["records"]}
    if set(audited) != set(expected):
        raise ValueError("Index audit does not cover the frozen corpus")
    for name, source in expected.items():
        row, proof = actual[name], audited[name]
        if (row["id"] != proof["document_id"] or row["status"] != proof["status"]
                or proof["sha256"] != source["sha256"]
                or proof["knowledge_base_id"] != kb_map[source["group"]]):
            raise ValueError("Live inventory changed since the index audit")
        if row["status"] == "ready" and row.get("publication", {}).get("state", "legacy_published") not in {"published", "legacy_published"}:
            raise ValueError("A ready source is not published")
    required = {name for case in cases for name in case["required_source_documents"]}
    if not required <= actual.keys():
        raise ValueError("Selected cases reference unknown source documents")
    unavailable = {name for name in required if name not in actual or actual[name]["status"] != "ready"}
    if unavailable and not record_unavailable:
        raise ValueError("A selected question requires an unavailable source")
    return unavailable


def snapshot_trace(case, response, snapshot, *, tenant_id, knowledge_base_ids, latency_ms):
    if snapshot["question"] != case["question"] or snapshot["snapshot_status"] != "captured":
        raise ValueError("Question changed or final context was not completely captured")
    if any(response.get(key) != value for key, value in snapshot["response"].items()):
        raise ValueError("Returned response does not match the persisted observation")
    context = snapshot["snapshot"]["context"]
    if context.get("incomplete_reason"):
        raise ValueError("Incomplete observed context")
    rows = context["items"]
    for row in rows:
        if str(row["knowledge_base_id"]) not in knowledge_base_ids:
            raise ValueError("Snapshot contains a source outside the requested scope")
        if hashlib.sha256(row["content"].encode()).hexdigest() != row["content_sha256"]:
            raise ValueError("Observed evidence fingerprint mismatch")
    return dict(id=case["id"], schema_version=1, origin="real_http_qa_and_owner_snapshot_api",
                question_hash=hashlib.sha256(case["question"].encode()).hexdigest(),
                query=snapshot["retrieval_question"], latency_ms=round(latency_ms, 2),
                scope=dict(tenant_id=tenant_id, knowledge_base_ids=sorted(knowledge_base_ids)),
                response=response, run_id=response["run_id"], request_id=snapshot["request_id"],
                pipeline=snapshot["config"].get("pipeline"),
                snapshot_model_usage=snapshot["snapshot"].get("model_usage", []),
                pipeline_observation=snapshot["snapshot"].get("pipeline"),
                stages=dict(context=rows))


def usage_observation(http, base, knowledge_base_id, request_id):
    result = http.get(base + "/api/knowledge-bases/" + knowledge_base_id + "/model-usage",
                      params=dict(request_id=request_id, limit=100), timeout=20)
    result.raise_for_status()
    payload = result.json()
    if payload.get("next_cursor") or any(row["request_id"] != request_id for row in payload["items"]):
        raise ValueError("Incomplete or mismatched physical-call observation")
    # Snapshot usage deliberately omits event IDs. Only the authenticated ledger
    # API supplies real IDs; never invent IDs to make a snapshot scoreable.
    return [dict(**{key: value for key, value in row.items() if key != "id"},
                 event_id=row["id"], persisted=True) for row in payload["items"]]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--tokens", type=Path, required=True, help="Private JSON: 知识管理员.access_token")
    parser.add_argument("--dataset-dir", type=Path, default=ROOT / "evals/manufacturing_lite")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-model-calls", action="store_true")
    parser.add_argument("--split", choices=("development", "test"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--case-ids", nargs="+")
    parser.add_argument("--max-questions", type=int, help="Required V2 question ceiling; not a token or monetary cost cap")
    parser.add_argument("--refresh-local-keycloak-token", action="store_true", help="Renew the existing local demo refresh token before expiry; never retry QA")
    parser.add_argument("--record-unavailable-sources", action="store_true", help="Explicitly record source-blocked Chinese cases as errors without QA; retain every selected case in the denominator")
    args = parser.parse_args(argv)
    if not args.allow_model_calls:
        parser.error("Real model calls require --allow-model-calls")
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    base = config["base_url"].rstrip("/")
    if base != "http://127.0.0.1:8000":
        raise ValueError("This runner only targets the existing local demo API")
    dataset, cases, frozen = select_cases(args.dataset_dir, split=args.split, limit=args.limit,
                                          case_ids=args.case_ids, max_questions=args.max_questions)
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise ValueError("Choose an empty output directory; previous runs are never overwritten")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.refresh_local_keycloak_token:
        http = LocalKeycloakSession(args.tokens)
    else:
        token = json.loads(args.tokens.read_text(encoding="utf-8-sig"))["知识管理员"]["access_token"]
        http = requests.Session()
        http.headers["Authorization"] = "Bearer " + token
    kb_map = config["knowledge_bases"]
    check = http.get(base + "/api/knowledge-bases", timeout=20)
    check.raise_for_status()
    if {row["id"] for row in check.json()["items"]} != set(kb_map.values()):
        raise ValueError("Authenticated scope does not exactly match the isolated three libraries")
    if set(kb_map) != {"公共参考", "生产工艺", "质量检验"}:
        raise ValueError("Expected exactly the three isolated manufacturing groups")
    unavailable = set()
    if args.record_unavailable_sources and (not frozen or frozen.get("edition") != "manufacturing-zh-v3"):
        parser.error("Source-blocked accounting is only supported for the frozen Chinese corpus")
    if frozen:
        if frozen.get("edition") == "manufacturing-zh-v3":
            unavailable = check_zh_inventory(http, base, kb_map, frozen, config, cases, record_unavailable=args.record_unavailable_sources)
        else:
            check_v2_inventory(http, base, kb_map, frozen, config)
    attempts, errors = [], 0
    with (args.output_dir / "traces.jsonl").open("x", encoding="utf-8") as stream:
        for case in cases:
            started = perf_counter()
            request_sent = False
            kb = kb_map.get(case["expected_scope"])
            scope_ids = {kb} if kb else set(kb_map.values())
            try:
                if unavailable.intersection(case["required_source_documents"]):
                    raise SourceUnavailable()
                request_sent = True
                answer = http.post(base + "/api/qa", json=dict(question=case["question"], knowledge_base_id=kb), timeout=180)
                answer.raise_for_status()
                response = answer.json()
                (args.output_dir / (case["id"] + "-response.json")).write_text(
                    json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                snapshot = http.get(base + "/api/qa/runs/" + response["run_id"], timeout=20)
                snapshot.raise_for_status()
                observed = snapshot.json()
                (args.output_dir / (case["id"] + "-snapshot.json")).write_text(
                    json.dumps(observed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                trace = snapshot_trace(case, response, observed, tenant_id=config["tenant_id"],
                                       knowledge_base_ids=scope_ids, latency_ms=(perf_counter() - started) * 1000)
                trace["stages"]["model_usage"] = usage_observation(http, base, sorted(scope_ids)[0], observed["request_id"])
            except Exception as exc:
                errors += 1
                trace = dict(id=case["id"], origin="real_http_qa_and_owner_snapshot_api",
                             error_type=type(exc).__name__, question_hash=hashlib.sha256(case["question"].encode()).hexdigest(),
                             latency_ms=round((perf_counter() - started) * 1000, 2))
                if isinstance(exc, requests.HTTPError) and exc.response is not None:
                    trace["http_status"] = exc.response.status_code
                    trace["request_id"] = exc.response.headers.get("X-Request-ID")
                if isinstance(exc, SourceUnavailable):
                    trace["blocked_sources"] = sorted(unavailable.intersection(case["required_source_documents"]))
            trace["request_sent"] = request_sent
            stream.write(json.dumps(trace, ensure_ascii=False) + "\n")
            stream.flush()
            attempts.append(dict(id=case["id"], error=trace.get("error_type"), run_id=trace.get("run_id"), request_sent=request_sent))
            print(json.dumps(attempts[-1]), flush=True)
    summary = dict(evaluation_type="real_http_acceptance_with_owner_context_snapshots", questions=len(cases),
                   completed=len(cases) - errors, errors=errors, dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
                   split=args.split or "baseline", selected_ids=[row["id"] for row in cases], max_questions=args.max_questions,
                   corpus_manifest_sha256=frozen["corpus_manifest_sha256"] if frozen else None,
                   auth_refreshes=getattr(http, "auth_refreshes", 0),
                   qa_request_retries=0, unobserved_stages=["vector", "keyword", "fusion", "rerank"], attempts=attempts)
    summary.update(qa_requests_sent=sum(r["request_sent"] for r in attempts),
        source_unavailable=sum(r["error"] == "SourceUnavailable" for r in attempts), record_unavailable_sources=args.record_unavailable_sources)
    (args.output_dir / "http-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
