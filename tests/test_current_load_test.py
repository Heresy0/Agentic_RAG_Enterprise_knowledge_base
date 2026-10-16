import base64
import csv
import json
from pathlib import Path
from uuid import uuid4

import pytest

from scripts.load_test_support import LoadCase, LoadTestConfig, LoadTestThresholds, build_load_test_report, load_cases, read_locust_stats
from scripts.load_test_telemetry import RunRecorder, counter_delta, current_response_error, parse_counters, preview_error, summarize_observations
from scripts.run_load_test import parse_args, remaining_token_seconds, run


def citation():
    return {"citation_id": "1", "knowledge_base_id": str(uuid4()), "document_id": str(uuid4()),
            "chunk_id": "child-1", "parent_id": "a" * 64}


def payload(answerable=True):
    return {"answer": "answer", "answerable": answerable, "citations": [citation()] if answerable else [],
            "refusal_reason": None if answerable else "insufficient evidence", "timings": {"total_ms": 100, "generation_ms": 80}}


def recorder():
    cases = [LoadCase("A", "simple", "question", True), LoadCase("B", "refusal", "unknown", False)]
    record = RunRecorder(cases, 2)
    record.before = record.after = {}
    return record


def complete(record, answers=(True, False), durations=(100, 200)):
    for actual, duration in zip(answers, durations):
        index, case = record.reserve()
        record.record_qa(index, case, status=200, successful=True, elapsed_ms=duration,
                         payload=payload(actual), request_id=f"r-{index}", status_label="200")
    return record.snapshot()


def test_current_workload_matches_original_question_text_and_annotations():
    root = Path(__file__).resolve().parents[1]
    original = {}
    for path in [root / 'evals/current/single_turn.jsonl']:
        original.update({row['id']: row for row in (json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip())})
    for case in load_cases(root / 'evals/current/load_workload.jsonl'):
        assert case.question == original[case.case_id]['question']
        assert case.expected_answerable == original[case.case_id]['answerable']


def test_all_scope_and_secure_configuration(tmp_path):
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(json.dumps({"id": "A", "group": "simple", "question": "ask", "answerable": True}))
    env = {"ENTERPRISE_KB_ACCESS_TOKEN": "a.b.c", "LOAD_TEST_SCOPE": "all", "LOAD_TEST_QUESTIONS_FILE": str(dataset),
           "LOAD_TEST_MAX_REQUESTS": "2", "LOAD_TEST_OBSERVATIONS_FILE": str(tmp_path / "obs.json")}
    config = LoadTestConfig.from_environment(env, project_root=tmp_path)
    assert config.knowledge_base_id is None and config.cases[0].case_id == "A"
    assert 'a.b.c' not in repr(config)
    for change in [{"LOAD_TEST_BASE_URL": "http://user:secret@localhost"}, {"LOAD_TEST_WAIT_MIN_SECONDS": "nan"},
                   {"LOAD_TEST_MAX_REQUESTS": "0"}, {"LOAD_TEST_SCOPE": "unfiltered"}]:
        with pytest.raises(ValueError):
            LoadTestConfig.from_environment({**env, **change}, project_root=tmp_path)


def test_request_reservations_are_bounded_before_dispatch_and_record_no_bodies():
    record = recorder()
    result = complete(record)
    assert record.reserve() is None
    assert summarize_observations(result)['checks']['bounded_run_complete']
    serialized = json.dumps(result)
    assert 'question' not in serialized and 'unknown' not in serialized and 'insufficient evidence' not in serialized


def test_empty_incomplete_and_duplicate_observations_never_pass():
    empty = recorder().snapshot()
    summary = summarize_observations(empty)
    assert not summary['checks']['bounded_run_complete'] and not summary['checks']['workload_covered']
    data = complete(recorder())
    data['qa'][1]['sequence'] = 0
    assert not summarize_observations(data)['checks']['bounded_run_complete']


def test_fast_false_refusal_does_not_mask_answerability_failure():
    data = complete(recorder(), answers=(False, False), durations=(1, 2))
    result = summarize_observations(data)
    assert result['qa']['failure_count'] == 0  # Valid HTTP responses, separate quality proxy.
    assert not result['checks']['answerability']
    assert result['groups']['simple']['answerability_mismatches'] == 1
    assert result['stages']['agent_decision_ms']['samples'] == 0
    assert result['stages']['agent_decision_ms']['p95_ms'] is None


@pytest.mark.parametrize('modify', [
    lambda p: p.update(citations=[]), lambda p: p['timings'].update(total_ms=float('nan')),
    lambda p: p['citations'][0].update(parent_id='../outside'), lambda p: p['citations'][0].update(knowledge_base_id=None),
])
def test_malformed_current_responses_are_rejected(modify):
    p = payload()
    assert current_response_error(p) is None
    modify(p)
    assert current_response_error(p) is not None


def test_preview_checks_source_version_headers_and_records_separately():
    c = citation()
    p = {'document_id': c['document_id'], 'chunk_id': c['chunk_id'], 'document_version': 1, 'content': 'body'}
    h = {'X-Request-ID': 'r', 'Cache-Control': 'no-store'}
    assert preview_error(200, p, c, h) is None
    assert preview_error(200, p, c, {'Cache-Control': 'no-store'}) is None
    assert preview_error(200, {**p, 'chunk_id': 'other'}, c, h)
    assert preview_error(200, p, c, {})
    record = recorder()
    record.preview_enabled = True
    assert record.claim_preview('A') and not record.claim_preview('A')
    data = complete(record)
    data['previews'] = [{'case_id': 'A', 'status_label': '200', 'successful': True, 'elapsed_ms': 1}]
    assert summarize_observations(data)['qa']['p95_ms'] == 195
    assert summarize_observations(data)['previews']['p95_ms'] == 1


def test_metrics_reset_and_absence_are_not_treated_as_zero_activity():
    before = parse_counters('enterprise_rag_routes_total{strategy="agentic",phase="plan"} 3\n')
    after = parse_counters('enterprise_rag_routes_total{strategy="agentic",phase="plan"} 5\n')
    assert counter_delta(before, after)['samples'][0]['delta'] == 2
    assert not counter_delta(after, before)['comparable']
    assert not counter_delta(before, {})['comparable']
    assert not counter_delta(None, after)['available']


def test_declared_unused_counters_are_a_valid_zero_baseline_but_missing_endpoint_is_not():
    declared = '# HELP enterprise_rag_routes_total Routes\n# TYPE enterprise_rag_routes_total counter\n'
    before = parse_counters(declared, require_declarations=True)
    assert before == {}
    after = parse_counters(declared + 'enterprise_rag_routes_total{strategy="standard",phase="plan"} 1\n', require_declarations=True)
    assert counter_delta(before, after)['comparable']
    assert counter_delta(before, after)['samples'][0]['delta'] == 1
    for invalid in ['', '# TYPE process_cpu_seconds_total counter\nprocess_cpu_seconds_total 0\n']:
        with pytest.raises(ValueError, match='declarations'):
            parse_counters(invalid, require_declarations=True)


def write_stats(path, count, failures=0, status=200):
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Type', 'Name', 'Request Count', 'Failure Count', 'Average Response Time', '95%'])
        writer.writerow(['POST', f'/api/qa [{status}]', count, failures, 100, 150])
        writer.writerow(['', 'Aggregated', count, failures, 100, 150])


def test_csv_missing_observations_and_200_failures_cannot_pass(tmp_path):
    stats = tmp_path / 'stats.csv'
    obs = tmp_path / 'obs.json'
    write_stats(stats, 3)
    obs.write_text(json.dumps(complete(recorder())))
    report = build_load_test_report(stats_path=stats, failures_path=None, thresholds=LoadTestThresholds(), scenario='steady', observations_path=obs)
    assert not report['checks']['csv_observations_match']
    write_stats(stats, 2, failures=2)
    data = complete(recorder())
    for row in data['qa']:
        row['successful'] = False
    obs.write_text(json.dumps(data))
    report = build_load_test_report(stats_path=stats, failures_path=None, thresholds=LoadTestThresholds(), scenario='steady', observations_path=obs)
    assert report['summary']['successful_request_count'] == 0
    assert not report['checks']['p95_latency']


def test_missing_failures_csv_cannot_hide_5xx_stats(tmp_path):
    stats = tmp_path / 'stats.csv'
    write_stats(stats, 2, failures=2, status=503)
    report = build_load_test_report(stats_path=stats, failures_path=None, thresholds=LoadTestThresholds(), scenario='steady')
    assert report['summary']['server_error_count'] == 2 and not report['checks']['server_errors']


def test_same_status_across_qa_and_preview_rows_keeps_counts_without_fabricating_percentiles(tmp_path):
    path = tmp_path / 'stats.csv'
    path.write_text('Type,Name,Request Count,Failure Count,Average Response Time,Max Response Time,95%\n'
                    'POST,/api/qa [200],2,0,1000,2000,2000\n'
                    'GET,/citation-preview [200],1,0,1,1,1\n'
                    ',Aggregated,3,0,667,2000,2000\n')
    result = read_locust_stats(path)
    assert result['outcomes']['200']['request_count'] == 3
    assert result['outcomes']['200']['average_ms'] == 667
    assert result['outcomes']['200']['p95_ms'] is None


def test_runner_requires_model_opt_in_checks_expiry_and_supports_network_free_dry_run(tmp_path):
    with pytest.raises(SystemExit):
        parse_args(['--output-dir', str(tmp_path)])
    args = parse_args(['--output-dir', str(tmp_path), '--dry-run'])
    assert run(args) == 0
    encoded = base64.urlsafe_b64encode(json.dumps({'exp': 300}).encode()).decode().rstrip('=')
    assert remaining_token_seconds(f'a.{encoded}.c', now=250) == 50
    for bad in ['bad', 'a.b.c']:
        with pytest.raises(ValueError):
            remaining_token_seconds(bad)


def test_real_locust_process_stops_at_request_cap_and_generates_body_free_report(tmp_path):
    """Synthetic local HTTP only: verifies harness plumbing, not real RAG or ACLs."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import os
    import subprocess
    import sys
    import threading

    counts = {'qa': 0, 'preview': 0}
    source = citation()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body, content_type='application/json'):
            encoded = body.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(encoded)))
            self.send_header('X-Request-ID', 'synthetic-r')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self):
            row = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            counts['qa'] += 1
            result = payload(row['question'] == 'supported')
            if result['answerable']:
                result['citations'] = [source]
            self.reply(json.dumps(result))

        def do_GET(self):
            if self.path == '/metrics':
                self.reply('# HELP enterprise_qa_requests_total QA\n# TYPE enterprise_qa_requests_total counter\n'
                           f'enterprise_qa_requests_total{{outcome="success",answerable="true"}} {counts["qa"]}\n', 'text/plain')
            else:
                counts['preview'] += 1
                self.reply(json.dumps({'document_id': source['document_id'], 'chunk_id': source['chunk_id'],
                                       'document_version': 1, 'content': 'SYNTHETIC_PRIVATE_BODY'}))

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    dataset = tmp_path / 'cases.jsonl'
    dataset.write_text('\n'.join(json.dumps(row) for row in [
        {'id': 'A', 'group': 'simple', 'question': 'supported', 'answerable': True},
        {'id': 'B', 'group': 'refusal', 'question': 'unsupported', 'answerable': False}]))
    env = os.environ.copy()
    env.update({'ENTERPRISE_KB_ACCESS_TOKEN': 'a.b.c', 'LOAD_TEST_SCOPE': 'all',
                'LOAD_TEST_BASE_URL': f'http://127.0.0.1:{server.server_port}',
                'LOAD_TEST_QUESTIONS_FILE': str(dataset), 'LOAD_TEST_CATEGORIES': '', 'LOAD_TEST_GROUPS': '',
                'LOAD_TEST_MODE': 'steady', 'LOAD_TEST_MAX_REQUESTS': '6',
                'LOAD_TEST_OBSERVATIONS_FILE': str(tmp_path / 'observations.json'),
                'LOAD_TEST_PREVIEW_CITATIONS': 'true', 'LOAD_TEST_EXPECT_PARENTS': 'true',
                'LOAD_TEST_WAIT_MIN_SECONDS': '0.01', 'LOAD_TEST_WAIT_MAX_SECONDS': '0.02'})
    root = Path(__file__).resolve().parents[1]
    try:
        result = subprocess.run([sys.executable, '-m', 'locust', '-f', 'tests/load/locustfile.py', '--headless',
            '-u', '2', '-r', '2', '-t', '15s', '--stop-timeout', '3', '--only-summary',
            '--csv', str(tmp_path / 'qa')], env=env, cwd=root, capture_output=True, timeout=40)
        assert result.returncode == 0, result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace')
        assert counts == {'qa': 6, 'preview': 1}
        observed = (tmp_path / 'observations.json').read_text(encoding='utf-8')
        assert 'SYNTHETIC_PRIVATE_BODY' not in observed and 'a.b.c' not in observed
        report = build_load_test_report(stats_path=tmp_path / 'final_stats.csv', failures_path=tmp_path / 'final_failures.csv',
            thresholds=LoadTestThresholds(min_requests=6, min_successful_requests=6), scenario='steady',
            observations_path=tmp_path / 'observations.json')
        assert report['passed'], [name for name, passed in report['checks'].items() if not passed]
        assert report['summary']['request_count'] == 6
        assert report['observations']['previews']['request_count'] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
