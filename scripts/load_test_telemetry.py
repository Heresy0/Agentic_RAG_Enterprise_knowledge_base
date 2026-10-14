"""Bounded, body-free HTTP observations for current RAG load tests."""
from datetime import datetime, timezone
import math
import re
import time

from prometheus_client.parser import text_string_to_metric_families


STAGES = frozenset({
    "query_planning_ms", "agent_decision_ms", "agent_tool_ms", "feedback_search_ms",
    "answer_regeneration_ms", "answer_repair_ms", "hybrid_retrieval_ms", "rerank_ms",
    "search_total_ms", "context_build_ms", "generation_ms", "validation_ms", "render_ms", "total_ms",
})
COUNTERS = frozenset({
    "enterprise_rag_routes_total", "enterprise_agent_actions_total", "enterprise_agent_runs_total",
    "enterprise_rag_repairs_total", "enterprise_rag_schema_repairs_total", "enterprise_rag_feedback_total",
    "enterprise_rag_feedback_final_total", "enterprise_rag_budget_requests_total",
    "enterprise_rag_terminal_results_total", "enterprise_qa_requests_total",
    "enterprise_model_calls_total", "enterprise_model_tokens_total", "enterprise_model_usage_unknown_total",
})


def distribution(values):
    values = sorted(float(value) for value in values)
    if not values:
        return {"samples": 0, "average_ms": None, "median_ms": None, "p95_ms": None, "p99_ms": None, "max_ms": None}

    def percentile(q):
        position = (len(values) - 1) * q
        lo = int(position)
        hi = min(lo + 1, len(values) - 1)
        return round(values[lo] + (values[hi] - values[lo]) * (position - lo), 3)

    return {"samples": len(values), "average_ms": round(sum(values) / len(values), 3),
            "median_ms": percentile(.5), "p95_ms": percentile(.95), "p99_ms": percentile(.99),
            "max_ms": max(values)}


def parse_counters(text, *, require_declarations=False):
    result = {}
    families = list(text_string_to_metric_families(text))
    # Labelled counters have HELP/TYPE declarations but no samples until first use.
    # That is a valid zero baseline, unlike an empty/unrelated metrics endpoint.
    if require_declarations and not any(family.type == "counter" and
            (family.name in COUNTERS or family.name + "_total" in COUNTERS) for family in families):
        raise ValueError("RAG counter declarations missing")
    for family in families:
        for sample in family.samples:
            if sample.name in COUNTERS and math.isfinite(sample.value):
                labels = tuple(sorted(sample.labels.items()))
                result[(sample.name, labels)] = float(sample.value)
    return result


def counter_delta(before, after):
    if before is None or after is None:
        return {"available": False, "comparable": False, "samples": []}
    comparable = all(key in after and after[key] >= value for key, value in before.items())
    samples = [{"name": key[0], "labels": dict(key[1]), "delta": value - before.get(key, 0)}
               for key, value in sorted(after.items()) if value != before.get(key, 0)]
    return {"available": True, "comparable": comparable, "samples": samples,
            "attribution": "process_wide_window_including_other_traffic"}


def current_response_error(payload):
    """Only format and source identity; never infer semantic faithfulness."""
    if not isinstance(payload, dict) or not isinstance(payload.get("answer"), str) or not payload["answer"].strip():
        return "invalid_answer_text"
    citations = payload.get("citations")
    if not isinstance(citations, list):
        return "invalid_citations_field"
    if payload.get("answerable") is True:
        if not citations:
            return "answer_without_citations"
        ids = set()
        from uuid import UUID
        for citation in citations:
            if not isinstance(citation, dict):
                return "invalid_citation_object"
            identifier = citation.get("citation_id")
            if not isinstance(identifier, str) or not identifier or identifier in ids:
                return "invalid_citation_id"
            ids.add(identifier)
            try:
                UUID(str(citation.get("knowledge_base_id")))
                UUID(str(citation.get("document_id")))
            except ValueError:
                return "invalid_citation_source"
            if not isinstance(citation.get("chunk_id"), str) or not citation["chunk_id"]:
                return "invalid_citation_chunk"
            if citation.get("parent_id") is not None and not re.fullmatch(r"[0-9a-f]{64}", str(citation["parent_id"])):
                return "invalid_citation_parent"
    elif payload.get("answerable") is False:
        if citations or not isinstance(payload.get("refusal_reason"), str) or not payload["refusal_reason"].strip():
            return "invalid_refusal_contract"
    else:
        return "invalid_answerable_field"
    timings = payload.get("timings")
    if not isinstance(timings, dict) or "total_ms" not in timings:
        return "invalid_timings_field"
    for key, value in timings.items():
        if key in STAGES and value is not None and (type(value) not in {int, float} or not math.isfinite(value) or value < 0):
            return "invalid_stage_timing"
    if timings.get("total_ms") is None:
        return "invalid_total_timing"
    return None


def preview_error(status, payload, citation, headers):
    headers = {key.lower(): value for key, value in headers.items()}
    if status != 200:
        return f"preview_http_status:{status}"
    if not isinstance(payload, dict):
        return "invalid_preview_object"
    if payload.get("document_id") != citation.get("document_id") or payload.get("chunk_id") != citation.get("chunk_id"):
        return "preview_source_mismatch"
    if not isinstance(payload.get("content"), str) or not payload["content"].strip():
        return "empty_preview_content"
    if type(payload.get("document_version")) is not int or payload["document_version"] < 1:
        return "invalid_preview_version"
    # QA has an explicit request-ID header; CitationPreview only promises no-store.
    if "no-store" not in headers.get("cache-control", "").lower():
        return "invalid_preview_headers"
    return None


class RunRecorder:
    """Single-process Locust recorder; reservation occurs before HTTP dispatch."""

    def __init__(self, cases, max_requests, *, previews=False, expect_parents=False, metadata=None):
        self.cases = cases
        self.max_requests = max_requests
        self.preview_enabled = previews
        self.expect_parents = expect_parents
        self.metadata = metadata or {}
        self.qa = []
        self.previews = []
        self.reserved = 0
        self.preview_claims = set()
        self.started = time.monotonic()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.before = self.after = None

    def reserve(self):
        if self.reserved >= self.max_requests:
            return None
        index = self.reserved
        self.reserved += 1
        return index, self.cases[index % len(self.cases)]

    def record_qa(self, index, case, *, status, successful, elapsed_ms, payload, request_id, status_label):
        timings = payload.get("timings", {}) if isinstance(payload, dict) else {}
        citations = payload.get("citations", []) if isinstance(payload, dict) else []
        actual = payload.get("answerable") if isinstance(payload, dict) else None
        valid = status == 200 and successful
        self.qa.append({
            "sequence": index, "case_id": case.case_id, "group": case.group,
            "status": status, "status_label": status_label, "successful": successful,
            "elapsed_ms": elapsed_ms, "request_id": request_id,
            "expected_answerable": case.expected_answerable,
            "actual_answerable": actual if type(actual) is bool and valid else None,
            "answerability_match": (actual == case.expected_answerable if valid and case.expected_answerable is not None else None),
            "citation_count": len(citations) if valid else 0,
            "parent_citation_count": sum(bool(c.get("parent_id")) for c in citations) if valid else 0,
            "timings": {k: v for k, v in timings.items() if k in STAGES and v is not None} if valid else {},
        })

    def claim_preview(self, case_id):
        if not self.preview_enabled or case_id in self.preview_claims:
            return False
        self.preview_claims.add(case_id)
        return True

    def snapshot(self):
        return {"schema_version": 1, "started_at": self.started_at,
                "duration_seconds": time.monotonic() - self.started,
                "max_requests": self.max_requests, "reserved_requests": self.reserved,
                "expected_case_ids": [case.case_id for case in self.cases],
                "preview_enabled": self.preview_enabled, "expect_parents": self.expect_parents,
                "preview_claim_count": len(self.preview_claims),
                "metadata": self.metadata, "qa": self.qa, "previews": self.previews,
                "metrics": counter_delta(self.before, self.after)}


def _http_summary(rows, duration):
    counts = {}
    outcomes = {}
    for row in rows:
        label = row["status_label"]
        counts[label] = counts.get(label, 0) + 1
    for label in counts:
        selected = [row for row in rows if row["status_label"] == label]
        # Invalid 200 responses stay failures. Their fast latency must not improve the success gate.
        accepted = [row["elapsed_ms"] for row in selected if row["successful"]]
        outcomes[label] = {"request_count": len(selected), "failure_count": sum(not row["successful"] for row in selected),
                           **distribution(accepted)}
    failures = sum(not row["successful"] for row in rows)
    return {"request_count": len(rows), "failure_count": failures,
            "failure_rate": failures / len(rows) if rows else 0,
            "requests_per_second": len(rows) / duration if duration > 0 else 0,
            **distribution([row["elapsed_ms"] for row in rows]), "status_counts": counts, "outcomes": outcomes}


def summarize_observations(data):
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported load observation schema")
    rows, previews = data["qa"], data["previews"]
    duration = data["duration_seconds"]
    sequences = [row["sequence"] for row in rows]
    groups = {}
    for group in sorted({row["group"] for row in rows}):
        selected = [row for row in rows if row["group"] == group]
        scored = [row for row in selected if row["answerability_match"] is not None]
        groups[group] = {**_http_summary(selected, duration), "answerability_samples": len(scored),
                         "answerability_mismatches": sum(not row["answerability_match"] for row in scored),
                         "answerable_count": sum(row["actual_answerable"] is True for row in selected),
                         "refusal_count": sum(row["actual_answerable"] is False for row in selected)}
    stages = {stage: distribution([row["timings"][stage] for row in rows if stage in row["timings"]])
              for stage in sorted(STAGES)}
    accepted_positive = [row for row in rows if row["successful"] and row["actual_answerable"] is True]
    checks = {
        "bounded_run_complete": len(rows) == data["reserved_requests"] == data["max_requests"] and len(set(sequences)) == len(rows),
        "workload_covered": set(data["expected_case_ids"]) <= {row["case_id"] for row in rows},
        "answerability": all(row["answerability_match"] is not False for row in rows),
        "parent_citations": (not data["expect_parents"] or (bool(accepted_positive) and all(row["parent_citation_count"] > 0 for row in accepted_positive))),
        "preview_contract": (not data["preview_enabled"] or (bool(previews) and len(previews) == data["preview_claim_count"] and all(row["successful"] for row in previews))),
        "metrics_available": data["metrics"]["available"],
        "metrics_comparable": data["metrics"]["comparable"],
    }
    return {"qa": _http_summary(rows, duration), "previews": _http_summary(previews, duration),
            "groups": groups, "stages": stages, "checks": checks,
            "metadata": data.get("metadata", {}), "metrics": data["metrics"],
            "completion": {"planned": data["max_requests"], "dispatched": data["reserved_requests"], "completed": len(rows)},
            "percentile_method": "linear_interpolation_of_observed_samples; small samples are descriptive"}
