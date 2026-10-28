"""Bounded claim assertions plus fingerprint-bound independent semantic reviews.

Passing regex/fact guards is never presented as a complete semantic judgment.
"""
from copy import deepcopy
import hashlib
import json
import re
import unicodedata

from .assertions import fact_assertion_match
from .metrics import document_key, normalize, summarize_records


SEMANTICS_VERSION = "2026-10-21-semantics-v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_assertions(assertions):
    if not isinstance(assertions, list):
        raise ValueError("semantic_assertions must be a list")
    ids = set()
    for rule in assertions:
        if (set(rule) - {"id", "kind", "patterns", "sources", "description"}
                or not isinstance(rule.get("id"), str) or not rule["id"] or rule["id"] in ids
                or rule.get("kind") not in ("require", "forbid")
                or not isinstance(rule.get("patterns"), list) or not rule["patterns"]):
            raise ValueError("Invalid semantic assertion")
        ids.add(rule["id"])
        if (not isinstance(rule.get("sources", []), list)
                or any(not isinstance(source, str) or not source.strip() for source in rule.get("sources", []))):
            raise ValueError("Semantic sources must be a list")
        for pattern in rule["patterns"]:
            if not isinstance(pattern, str) or not 1 <= len(pattern) <= 2000:
                raise ValueError("Invalid semantic pattern")
            re.compile(pattern)


def review_binding(case, response, context):
    return dict(question_sha256=digest(case["question"]), response_sha256=digest(response),
                context_sha256=digest(context), criteria_sha256=digest(case),
                semantics_version=SEMANTICS_VERSION)


def _supported_line(line, sources, response, context):
    ids = set(re.findall(r"\[([^\[\]\n]+)\]", line))
    supported = set()
    for citation in response.get("citations", []):
        if citation.get("citation_id") not in ids:
            continue
        for row in context:
            if (row.get("citation_id") == citation.get("citation_id")
                    and document_key(row.get("document_name", "")) == document_key(citation.get("document_name", ""))
                    and (not citation.get("chunk_id") or row.get("chunk_id") == citation["chunk_id"])
                    and (not citation.get("knowledge_base_id") or row.get("knowledge_base_id") == str(citation["knowledge_base_id"]))
                    and row.get("parent_id") == citation.get("parent_id")):
                supported.add(document_key(row["document_name"]))
    return {document_key(name) for name in sources} <= supported


def score_semantics(case, response, context):
    assertions = case.get("semantic_assertions", [])
    validate_assertions(assertions)
    text = unicodedata.normalize("NFKC", response["answer"])
    lines = [re.sub(r"[ \t]+", "", line) for line in text.splitlines() if line.strip()]
    checks = []
    for rule in assertions:
        matches = [line for line in lines if any(re.search(pattern, line) for pattern in rule["patterns"])]
        sources = rule.get("sources", [])
        if rule["kind"] == "forbid":
            passed = not matches
        else:
            passed = bool(matches) and (not sources or context is not None and
                any(_supported_line(line, sources, response, context) for line in matches))
        state = "passed" if passed else "partial" if sources and context is None and matches else "failed"
        checks.append(dict(id=rule["id"], kind=rule["kind"], status=state,
                           description=rule.get("description"),
                           reason="context_not_observed" if state == "partial" else
                                  "forbidden_statement" if matches and rule["kind"] == "forbid" else
                                  "required_statement_or_source_binding_missing" if not passed else None))
    for fact, rule in case.get("fact_assertions", {}).items():
        if fact not in case.get("required_facts", []):
            continue
        options = [normalize(value) for value in [fact, *case.get("fact_alternatives", {}).get(fact, [])]]
        normalized_rule = dict(rule, contradictions=[normalize(value) for value in rule.get("contradictions", [])])
        passed, _ = fact_assertion_match(normalize(text), options, normalized_rule)
        checks.append(dict(id="fact:" + fact, kind="fact_polarity", status="passed" if passed else "failed"))
    failed = any(row["status"] == "failed" for row in checks)
    return dict(status="failed" if failed else "partial" if checks else "skipped",
                bounded_checks=checks, bounded_checks_passed=bool(checks) and all(row["status"] == "passed" for row in checks),
                review_status="not_reviewed", context_observed=context is not None,
                review_binding=review_binding(case, response, context),
                semantic_correctness="requires_independent_review", version=SEMANTICS_VERSION)


def apply_semantic_reviews(quality, path):
    """Import local review JSONL; stale, duplicate or unobserved answers cannot pass."""
    result = deepcopy(quality)
    records = {row["id"]: row for row in result.get("records", [])}
    seen = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        review = json.loads(line)
        identifier = review.get("id")
        if identifier in seen or identifier not in records:
            raise ValueError("Duplicate or unknown semantic review ID")
        seen.add(identifier)
        metric = records[identifier].get("metrics", {}).get("semantics")
        if metric is None:
            raise ValueError("Semantic review has no observed response")
        if not metric["context_observed"]:
            raise ValueError("Semantic review has no observed context")
        if review.get("binding") != metric["review_binding"]:
            raise ValueError("Semantic review fingerprint mismatch")
        for payload, fingerprint in (("question", "question_sha256"), ("response", "response_sha256"),
                                     ("actual_context", "context_sha256"), ("criteria", "criteria_sha256")):
            if payload in review and digest(review[payload]) != metric["review_binding"][fingerprint]:
                raise ValueError("Semantic review payload fingerprint mismatch")
        if (review.get("verdict") not in ("passed", "failed", "uncertain")
                or not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip()
                or not isinstance(review.get("reason"), str) or not review["reason"].strip()):
            raise ValueError("Semantic review requires verdict, reviewer and reason")
        metric["review_status"] = review["verdict"]
        metric["review"] = {key: review[key] for key in ("reviewer", "reason", "verdict")}
        bounded_states = [row["status"] for row in metric["bounded_checks"]]
        metric["status"] = ("failed" if "failed" in bounded_states or review["verdict"] == "failed" else
                            "partial" if "partial" in bounded_states or review["verdict"] == "uncertain" else "passed")
        metric["semantic_correctness"] = "reviewed_" + review["verdict"]
    return result


def semantics_layer(quality):
    records = []
    for record in quality.get("records", []):
        metric = record.get("metrics", {}).get("semantics")
        state = "error" if record["status"] == "error" or record.get("scope_violation_count") else metric["status"] if metric else "skipped"
        records.append(dict(id=record["id"], status=state, metrics=metric,
                            model_usage=record.get("metrics", {}).get("model_usage")))
    observed = [row["metrics"] for row in records if row["metrics"]]
    return dict(status="completed", summary=summarize_records(records), records=records,
                evaluation_type=quality.get("evaluation_type"), metrics=dict(
                    observed_cases=len(observed),
                    assertion_covered_cases=sum(bool(row["bounded_checks"]) for row in observed),
                    reviewed_cases=sum(row["review_status"] != "not_reviewed" for row in observed)),
                limitation="Bounded assertions detect annotated failures; complete semantic correctness requires "
                           "a fingerprint-bound independent review. Unreviewed and uncertain are not passes.")
