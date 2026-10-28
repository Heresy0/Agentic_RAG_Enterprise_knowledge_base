"""Run the 20 manufacturing questions through real authenticated HTTP endpoints.

Snapshots are read through the same owner's API. No QA-request retries, account
creation or source/criteria edits. Raw authorized context stays in local output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.evaluate_answers import load_dataset


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
    args = parser.parse_args(argv)
    if not args.allow_model_calls:
        parser.error("Real model calls require --allow-model-calls")
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    base = config["base_url"].rstrip("/")
    if base != "http://127.0.0.1:8000":
        raise ValueError("This runner only targets the existing local demo API")
    dataset = args.dataset_dir / "single_turn.jsonl"
    cases = load_dataset(dataset)
    if len(cases) != 20 or {row["id"] for row in cases} != {f"MFG-Q{i:02}" for i in range(1, 21)}:
        raise ValueError("Expected the frozen 20-question manufacturing acceptance set")
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise ValueError("Choose an empty output directory; previous runs are never overwritten")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    token = json.loads(args.tokens.read_text(encoding="utf-8-sig"))["知识管理员"]["access_token"]
    http = requests.Session()
    http.headers["Authorization"] = "Bearer " + token
    kb_map = config["knowledge_bases"]
    check = http.get(base + "/api/knowledge-bases", timeout=20)
    check.raise_for_status()
    if {row["id"] for row in check.json()["items"]} != set(kb_map.values()):
        raise ValueError("Authenticated scope does not exactly match the isolated three libraries")
    attempts, errors = [], 0
    with (args.output_dir / "traces.jsonl").open("x", encoding="utf-8") as stream:
        for case in cases:
            started = perf_counter()
            kb = kb_map.get(case["expected_scope"])
            scope_ids = {kb} if kb else set(kb_map.values())
            try:
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
            stream.write(json.dumps(trace, ensure_ascii=False) + "\n")
            stream.flush()
            attempts.append(dict(id=case["id"], error=trace.get("error_type"), run_id=trace.get("run_id")))
            print(json.dumps(attempts[-1]), flush=True)
    summary = dict(evaluation_type="real_http_acceptance_with_owner_context_snapshots", questions=len(cases),
                   completed=len(cases) - errors, errors=errors, dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
                   qa_request_retries=0, unobserved_stages=["vector", "keyword", "fusion", "rerank"], attempts=attempts)
    (args.output_dir / "http-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
