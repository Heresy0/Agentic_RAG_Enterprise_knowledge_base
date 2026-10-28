"""Export observed answers and frozen criteria for independent review; no model calls."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.evaluate_answers import load_dataset
from backend.app.evaluation.quality_policy import apply_quality_policy
from backend.app.evaluation.scoring_rules import apply_scoring_rules
from backend.app.evaluation.runner import score_trace


def prepare(dataset, source_report, traces, output):
    if output.exists():
        raise ValueError("Choose a new review file; existing reviews are never overwritten")
    dataset_file = dataset / "single_turn.jsonl"
    cases = apply_scoring_rules(load_dataset(dataset_file), dataset / "scoring_rules.json")
    cases, policy_sha = apply_quality_policy(cases, dataset / "quality_policy.json", dataset_file)
    source = json.loads(source_report.read_text(encoding="utf-8"))
    if source.get("dataset_sha256") != hashlib.sha256(dataset_file.read_bytes()).hexdigest():
        raise ValueError("Source report dataset fingerprint mismatch")
    if source.get("quality_policy_sha256") not in (None, policy_sha):
        raise ValueError("Source report quality policy mismatch")
    source_records = source.get("layers", {}).get("answer", {}).get("records", [])
    source_answers = {row["id"]: row["response"] for row in source_records if row.get("response")}
    semantic_records = source.get("layers", {}).get("semantics", {}).get("records", [])
    source_bindings = {row["id"]: row["metrics"]["review_binding"]
                       for row in semantic_records if row.get("metrics")}
    selected = set(source.get("selected_case_ids") or [*source_answers, *source_bindings])
    by_id = {case["id"]: case for case in cases}
    if selected - by_id.keys():
        raise ValueError("Source report contains unknown cases")
    observed = {}
    for line in traces.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            row = json.loads(line)
            if row["id"] in observed:
                raise ValueError("Duplicate trace ID")
            observed[row["id"]] = row
    templates = []
    for identifier in by_id:
        trace = observed.get(identifier, {})
        if identifier not in selected or trace.get("error_type") or "response" not in trace:
            continue
        case = by_id[identifier]
        if trace.get("question_hash") != hashlib.sha256(case["question"].encode()).hexdigest():
            raise ValueError("Trace question fingerprint mismatch")
        if "context" not in trace.get("stages", {}):
            raise ValueError("Review requires observed final context")
        record = score_trace(case, trace, include_answer=True)
        binding = record["metrics"]["semantics"]["review_binding"]
        if record["scope_violation_count"]:
            raise ValueError("Review pack refuses scope violations")
        if identifier in source_answers and source_answers[identifier] != trace["response"]:
            raise ValueError("Source report and trace response differ")
        if identifier in source_bindings and source_bindings[identifier] != binding:
            raise ValueError("Source semantic review fingerprint differs")
        if identifier not in source_answers and identifier not in source_bindings:
            raise ValueError("Source report has no matching answer observation")
        templates.append(dict(id=identifier, binding=binding, verdict="uncertain", reviewer="", reason="",
            question=case["question"], criteria=case, reference_answer=case["reference_answer"],
            required_facts=case["required_facts"], semantic_checks=case.get("semantic_checks", []),
            forbidden_claims=case.get("forbidden_claims", []),
            semantic_assertions=case.get("semantic_assertions", []), response=trace["response"],
            actual_context=trace["stages"]["context"],
            review_instructions="核对事实与对象、数值/单位/条件、否定与比较方向、逐结论引用、核心结论是否先出现。"
                                "不能确定则uncertain；不要把规则通过当语义正确。填写reviewer和reason。"))
    if not templates:
        raise ValueError("No observed answers available for review")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        for template in templates:
            stream.write(json.dumps(template, ensure_ascii=False) + "\n")
    return dict(review_items=len(templates), unobserved_cases=len(selected) - len(templates), model_calls=0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=ROOT / "evals/current")
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--trace-input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(prepare(args.dataset_dir, args.source_report, args.trace_input, args.output)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
