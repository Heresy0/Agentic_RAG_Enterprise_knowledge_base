from copy import deepcopy

from backend.app.evaluation.metrics import evidence_metrics as original_metrics
from backend.app.evaluation.retrieval_metrics import evidence_metrics, summarize_stages
from backend.app.evaluation.reporting import compare_baseline
from scripts.evaluate_answers import summarize_trace_timings
from scripts import run_manufacturing_http_acceptance as http_runner


def evidence(text):
    return dict(document='规范.md', text=text)


def row(text):
    return dict(document_name='规范.md', content=text)


def test_fixed_k_excludes_late_hits_and_counts_each_required_evidence_once():
    expected = [evidence('条件甲'), evidence('条件乙')]
    rows = [row('无关'), row('条件甲'), row('条件甲'), *[row('无关') for _ in range(5)], row('条件乙')]
    result = evidence_metrics(expected, rows)
    assert result['evidence_recall'] == 1
    assert result['at_k']['1']['hit'] is False
    assert result['at_k']['1']['reciprocal_rank'] == 0
    assert result['at_k']['3']['reciprocal_rank'] == .5
    assert result['at_k']['8']['evidence_recall'] == .5
    assert result['at_k']['8']['all_evidence_present'] is False


def test_accepted_alternative_is_evaluated_within_each_cutoff():
    expected = [evidence('条件甲'), evidence('条件乙'), evidence('条件丙')]
    result = evidence_metrics(expected, [row('条件甲'), row('替代完整条款'), row('条件乙'), row('条件丙')],
                              alternative_sets=[[evidence('替代完整条款')]])
    assert result['expected_evidence_count'] == 3
    assert result['at_k']['3']['evidence_recall'] == 1
    assert result['at_k']['3']['reciprocal_rank'] == .5


def test_legacy_metrics_and_negative_case_semantics_are_preserved():
    expected, rows = [evidence('条件甲')], [row('条件甲')]
    observed = evidence_metrics(expected, rows)
    observed.pop('at_k')
    assert observed == original_metrics(expected, rows)
    assert evidence_metrics([], rows) == original_metrics([], rows)
    summary = summarize_stages([dict(metrics=dict(context=evidence_metrics(expected, rows))),
                                dict(metrics=dict(context=original_metrics(expected, rows)))])
    assert summary['context']['scored_cases'] == 2
    assert summary['context']['at_k']['8']['scored_cases'] == 1
    assert summary['vector']['at_k']['8']['evidence_recall'] is None


def test_http_timer_stops_before_snapshot_collection(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(http_runner, 'perf_counter', lambda: clock[0])
    response = object()
    class Client:
        def post(self, url, *, json, timeout):
            assert url == 'http://127.0.0.1:8001/api/qa' and timeout == 180
            clock[0] += .25
            return response
    actual, latency = http_runner.timed_qa_request(Client(), 'http://127.0.0.1:8001', {'question': '问题'})
    clock[0] += 50
    assert actual is response and latency == 250


def test_old_observation_latency_is_not_relabelled_http_p95():
    records = [dict(latency_ms=1000, latency_scope='http_qa_response', response=dict(timings=dict(total_ms=100))),
               dict(latency_ms=200, response=dict(timings=dict(total_ms=50))),
               dict(latency_ms=30, latency_scope='local_backend_services', response=None),
               dict(error_type='Timeout', latency_ms=180000)]
    before = deepcopy(records)
    summary = summarize_trace_timings(records)
    assert summary['http_qa']['count'] == 1 and summary['http_qa']['p95_ms'] == 1000
    assert summary['local_services']['count'] == 1
    assert summary['unclassified_trace_timings'] == 1
    assert summary['execution_errors'] == 1
    assert summary['backend_stages']['total_ms']['count'] == 2
    assert summary['backend_stages']['total_ms']['p95_ms'] == 97.5
    assert records == before


def test_auth_refresh_is_outside_qa_timer(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(http_runner, 'perf_counter', lambda: clock[0])
    class Client(http_runner.LocalKeycloakSession):
        def __init__(self):
            pass
        def refresh_if_needed(self):
            clock[0] += 10
        def post(self, *args, **kwargs):
            clock[0] += .5
            return object()
    _, latency = http_runner.timed_qa_request(Client(), 'http://127.0.0.1:8001', {})
    assert latency == 500


def test_fixed_k_baselines_require_same_metric_implementation():
    baseline = dict(retrieval_metrics_sha256='v1', layers=dict(context=dict(
        metrics=dict(at_k={'8': dict(mrr=.5)}))))
    candidate = deepcopy(baseline)
    candidate['layers']['context']['metrics']['at_k']['8']['mrr'] = .75
    result = compare_baseline(candidate, baseline)
    assert result['status'] == 'compared'
    assert result['changes'] == [dict(layer='context', metric='mrr@8', baseline=.5, current=.75, delta=.25)]
    candidate['retrieval_metrics_sha256'] = 'v2'
    assert compare_baseline(candidate, baseline)['mismatches'] == ['retrieval_metrics_sha256']
