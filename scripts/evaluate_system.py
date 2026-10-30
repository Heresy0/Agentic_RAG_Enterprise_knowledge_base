"""Unified offline/replay/live entry point; live model calls require opt-in."""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.evaluation.offline import evaluate_parsing, evaluate_security_unit, read_performance_report
from backend.app.evaluation.reporting import LAYERS, compare_baseline, quality_layers, skipped, write_report
from backend.app.evaluation.runner import evaluate_local, replay
from backend.app.evaluation.http_runner import evaluate_conversations, evaluate_permissions
from backend.app.evaluation.conversation_runner import evaluate_local_conversations
from scripts.evaluate_answers import load_dataset, summarize_trace_timings
from backend.app.evaluation.metrics import SCORER_VERSION
from backend.app.evaluation.scoring_rules import apply_scoring_rules
from backend.app.evaluation.suite import load_suite, select_groups, group_scores
from backend.app.evaluation.quality_policy import EVALUATOR_VERSION, apply_quality_policy
from backend.app.evaluation.semantics import apply_semantic_reviews


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("offline", "replay", "live"), default="offline")
    parser.add_argument("--layers", default="all", help="all or comma-separated layer names")
    parser.add_argument("--dataset-dir", type=Path, default=ROOT / 'evals/current')
    parser.add_argument('--group',action='append',help='Repeat main/multihop/completeness; default: all single-turn groups')
    parser.add_argument("--corpus", type=Path, default=ROOT / "output/knowledge-corpus-100")
    parser.add_argument("--config", type=Path, default=ROOT / "evals/system_config.example.json")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--trace-input", type=Path)
    parser.add_argument("--performance-report", type=Path)
    parser.add_argument("--baseline-report", type=Path, help="Compare a like-for-like previous system report")
    parser.add_argument("--quality-policy", type=Path, help="Additional versioned ranking/semantic checks; defaults to dataset-dir/quality_policy.json")
    parser.add_argument("--semantic-reviews", type=Path, help="Fingerprint-bound independent review JSONL for observed final answers")
    parser.add_argument("--limit", type=int, help="Limit each case collection, not total model calls")
    parser.add_argument("--case-ids", nargs='+', help="Only these single-turn IDs; applied before --limit")
    parser.add_argument("--conversation-ids", nargs='+', help="Only these conversation group IDs; applied before --limit")
    parser.add_argument('--conversation-backend', choices=('http', 'local'), default='http',
                        help='HTTP by default; local services support opt-in per-turn diagnostic snapshots')
    parser.add_argument("--ocr", action="store_true", help="Run real local OCR if runtime is available")
    parser.add_argument("--allow-model-calls", action="store_true")
    parser.add_argument("--allow-conversation-writes", action="store_true")
    parser.add_argument("--save-traces", action="store_true", help="Save sensitive authorized source/context snapshots locally")
    parser.add_argument("--strict", action="store_true", help="Exit 2 if requested checks are incomplete")
    args = parser.parse_args(argv)
    args.layers = list(LAYERS) if args.layers == "all" else list(dict.fromkeys(args.layers.split(",")))
    if not args.layers or any(layer not in LAYERS for layer in args.layers):
        parser.error("Unknown layer; choose: " + ",".join(LAYERS))
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.mode == "replay" and not args.trace_input:
        parser.error("--mode replay requires --trace-input")
    if args.semantic_reviews and (args.mode == "offline" or "semantics" not in args.layers):
        parser.error("--semantic-reviews requires live/replay mode and the semantics layer")
    live_model_layers = {"retrieval", "rerank", "context", "answer", "ranking", "semantics", "conversations", "security"}
    if args.mode == "live" and live_model_layers.intersection(args.layers) and not args.allow_model_calls:
        parser.error("Live embedding/reranking/answer calls require --allow-model-calls")
    return args


def main(argv=None):
    args = parse_args(argv)
    directory = args.output_dir or ROOT / "evals/reports/system" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "report.json").exists() or (directory / "report.md").exists():
        raise ValueError("Choose a new output directory; previous reports are not overwritten")
    rules_path = args.dataset_dir / 'scoring_rules.json'
    cases = apply_scoring_rules(load_dataset(args.dataset_dir / "single_turn.jsonl"), rules_path)
    suite=load_suite(args.dataset_dir,cases)
    if args.quality_policy and not args.quality_policy.is_file():
        raise ValueError("Explicit quality policy file does not exist")
    cases, policy_sha = apply_quality_policy(cases, args.quality_policy or args.dataset_dir / "quality_policy.json",
                                            args.dataset_dir / "single_turn.jsonl")
    full_case_count = len(cases)
    cases=select_groups(cases,suite,args.group)
    if args.case_ids:
        unknown = set(args.case_ids) - {case['id'] for case in cases}
        if unknown:
            raise ValueError('Unknown single-turn IDs: ' + ','.join(sorted(unknown)))
        cases = [case for case in cases if case['id'] in args.case_ids]
    if args.limit:
        cases = cases[:args.limit]
    report = dict(schema_version=1, scorer_version=SCORER_VERSION,
                  evaluator_version=EVALUATOR_VERSION, quality_policy_sha256=policy_sha,
                  scoring_rules_sha256=hashlib.sha256(rules_path.read_bytes()).hexdigest() if rules_path.is_file() else None,
                  mode=args.mode, selected_cases=len(cases), available_cases=full_case_count,
                  selected_case_ids=[case["id"] for case in cases],
                  requested_conversation_ids=args.conversation_ids,
                  conversation_backend=args.conversation_backend if 'conversations' in args.layers else None,
                  conversations_sha256=hashlib.sha256((args.dataset_dir / 'conversations.json').read_bytes()).hexdigest()
                      if 'conversations' in args.layers else None,
                  corpus_manifest_sha256=hashlib.sha256((args.corpus / "manifest.json").read_bytes()).hexdigest()
                      if (args.corpus / "manifest.json").is_file() else None,
                  scope_config_sha256=None,
                  dataset_sha256=hashlib.sha256((args.dataset_dir / "single_turn.jsonl").read_bytes()).hexdigest(),
                  live_model_calls_enabled=args.mode == "live" and args.allow_model_calls,
                  business_permission_changes=False, layers={name: skipped("not_selected") for name in args.layers})
    report.update(suite_sha256=hashlib.sha256((args.dataset_dir/'suite.json').read_bytes()).hexdigest() if suite else None,
                  selected_groups=list(dict.fromkeys(args.group)) if args.group else None)
    quality=None
    def run_layer(name, operation):
        print("Evaluating " + name, file=sys.stderr, flush=True)
        try:
            report["layers"][name] = operation()
        except Exception as exc:
            # Exceptions may contain database URLs or HTTP headers. Never write
            # exception strings/tracebacks from credentialed evaluation to reports.
            report["layers"][name] = dict(status="error", error_type=type(exc).__name__)
    if "parsing" in args.layers:
        run_layer("parsing", lambda: evaluate_parsing(args.corpus, cases, ocr=args.ocr))
    quality_requested = {"retrieval", "rerank", "context", "answer", "ranking", "semantics"}.intersection(args.layers)
    include_answer = bool({"answer", "semantics"}.intersection(quality_requested))
    if args.mode == "replay" and quality_requested:
        quality = {}
        run_layer("_quality", lambda: replay(cases, args.trace_input, include_answer=include_answer))
        quality = report["layers"].pop("_quality")
        if quality.get("status") == "error":
            report["layers"].update({name: quality for name in quality_requested})
        else:
            report["layers"].update({name: value for name, value in quality_layers(quality).items() if name in quality_requested})
    elif args.mode == "live":
        config = json.loads(args.config.read_text(encoding="utf-8-sig"))
        report["scope_config_sha256"] = hashlib.sha256(json.dumps(
            {key: config.get(key) for key in ("base_url", "knowledge_bases", "profiles")},
            sort_keys=True).encode()).hexdigest()
        if quality_requested or "index" in args.layers:
            run_layer("_quality", lambda: evaluate_local(cases if quality_requested else [], config,
                        include_answer=include_answer,
                        include_index="index" in args.layers,
                        expected_documents=json.loads((args.corpus / "manifest.json").read_text(encoding="utf-8"))["documents"]
                            if "index" in args.layers else (),
                        save_traces=directory / "traces.jsonl" if args.save_traces else None))
            quality = report["layers"].pop("_quality")
            if quality.get("status") == "error":
                report["layers"].update({name: quality for name in quality_requested | ({"index"} if "index" in args.layers else set())})
            else:
                report["runtime"] = quality.get("runtime")
                report["layers"].update({name: value for name, value in quality_layers(quality).items() if name in quality_requested})
                if "index" in args.layers:
                    report["layers"]["index"] = quality["index"]
        if "conversations" in args.layers:
            if args.allow_conversation_writes:
                sessions = json.loads((args.dataset_dir / "conversations.json").read_text(encoding="utf-8"))
                if args.conversation_ids:
                    unknown = set(args.conversation_ids) - {row['id'] for row in sessions}
                    if unknown:
                        raise ValueError('Unknown conversation group IDs: ' + ','.join(sorted(unknown)))
                    sessions = [row for row in sessions if row['id'] in args.conversation_ids]
                selected_sessions = sessions[:args.limit] if args.limit else sessions
                if args.conversation_backend == 'local':
                    run_layer('conversations', lambda: evaluate_local_conversations(selected_sessions, config,
                              save_traces=directory / 'conversation_traces.jsonl' if args.save_traces else None))
                else:
                    run_layer("conversations", lambda: evaluate_conversations(selected_sessions, config))
            else:
                report["layers"]["conversations"] = skipped("requires_allow_conversation_writes")
        if "security" in args.layers:
            permission = json.loads((args.dataset_dir / "permissions.json").read_text(encoding="utf-8"))
            if args.limit:
                permission["cases"] = permission["cases"][:args.limit]
            run_layer("security", lambda: evaluate_permissions(permission, config))
    else:
        for name in quality_requested:
            report["layers"][name] = skipped("requires_live_opt_in_or_actual_trace_replay")
    if "index" in args.layers and args.mode != "live":
        report["layers"]["index"] = skipped("requires_authenticated_read_only_database_access")
    if "conversations" in args.layers and args.mode != "live":
        report["layers"]["conversations"] = skipped("requires_real_http_session_or_separate_unit_tests")
    if "security" in args.layers and args.mode != "live":
        run_layer("security", lambda: evaluate_security_unit(ROOT, directory))
        report["layers"]["security"]["limitation"] = "Isolated mocked/SQLite regressions only; deployed-profile ACL tests have not run."
    if "performance" in args.layers:
        run_layer("performance", lambda: read_performance_report(args.performance_report))
    if quality and quality.get("records") is not None and args.semantic_reviews:
        quality = apply_semantic_reviews(quality, args.semantic_reviews)
        report["layers"]["semantics"] = quality_layers(quality)["semantics"]
    if suite and quality_requested and quality and quality.get('records') is not None:
        report['group_scores']=group_scores(quality,suite,report['selected_case_ids'],quality_requested)
    if quality and quality.get("records") is not None:
        report["timings"] = summarize_trace_timings(quality["records"])
        report["retrieval_metrics_sha256"] = hashlib.sha256(
            (ROOT / "backend/app/evaluation/retrieval_metrics.py").read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    if args.baseline_report:
        baseline = json.loads(args.baseline_report.read_text(encoding="utf-8"))
        report["baseline_comparison"] = compare_baseline(report, baseline)
    write_report(report, directory)
    print(json.dumps(dict(overall_status=report["overall_status"], report_directory=str(directory)), ensure_ascii=True))
    if report["overall_status"] == "failed":
        return 1
    return 2 if args.strict and report["overall_status"] == "incomplete" else 0


if __name__ == "__main__":
    raise SystemExit(main())
