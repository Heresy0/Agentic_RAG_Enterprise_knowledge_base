"""Versioned additional checks; never rewrite questions, archived gold or r10 scores."""
from copy import deepcopy
import hashlib
import json

from .semantics import validate_assertions


EVALUATOR_VERSION = "2026-10-21-quality-v1"


def apply_quality_policy(cases, path, dataset_path):
    if not path.is_file():
        return cases, None
    raw = path.read_bytes()
    policy = json.loads(raw)
    if (policy.get("version") != EVALUATOR_VERSION or
            policy.get("dataset_sha256") != hashlib.sha256(dataset_path.read_bytes()).hexdigest()):
        raise ValueError("Quality policy dataset/version fingerprint mismatch")
    patches = policy.get("cases", {})
    if set(patches) - {case["id"] for case in cases}:
        raise ValueError("Quality policy contains unknown case IDs")
    result = deepcopy(cases)
    for case in result:
        patch = patches.get(case["id"], {})
        if set(patch) - {"semantic_assertions", "ranking_checks", "relevance_judgments"}:
            raise ValueError("Quality policy cannot change gold facts or access scope")
        validate_assertions(patch.get("semantic_assertions", []))
        ranking = patch.get("ranking_checks", {})
        if (set(ranking) - {"max_first_relevant_rank"} or
                type(ranking.get("max_first_relevant_rank", 1)) is not int or
                ranking.get("max_first_relevant_rank", 1) < 1):
            raise ValueError("Invalid ranking check")
        case.update(patch)
    return result, hashlib.sha256(raw).hexdigest()
