import os
import random
import sys
import json
import csv
from urllib.parse import quote

from pathlib import Path

from locust import HttpUser, between, task, events
from locust.exception import StopUser
from locust.runners import LocalRunner
from locust.stats import StatsCSV, PERCENTILES_TO_REPORT
import gevent
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.load_test_support import (  # noqa: E402
    LoadTestConfig,
    validate_qa_response,
)
from scripts.load_test_telemetry import RunRecorder, parse_counters, current_response_error, preview_error


CONFIG = LoadTestConfig.from_environment(
    os.environ,
    project_root=PROJECT_ROOT,
)
RECORDER = None


def metrics_snapshot():
    try:
        response = requests.get(CONFIG.base_url + "/metrics", timeout=5, allow_redirects=False)
        if response.status_code == 200:
            return parse_counters(response.text, require_declarations=True)
    except (requests.RequestException, ValueError):
        pass
    return None


@events.test_start.add_listener
def start_observations(environment, **kwargs):
    global RECORDER
    if not CONFIG.observations_path:
        return
    if not isinstance(environment.runner, LocalRunner):
        raise ValueError("有界压测只支持单进程运行，不支持 master/worker")
    metadata = {"profile": os.environ.get("LOAD_TEST_PROFILE", "custom"),
                "scope": "all_authorized" if CONFIG.knowledge_base_id is None else "single",
                "mode": CONFIG.mode, "users": environment.parsed_options.num_users,
                "source_revision": os.environ.get("LOAD_TEST_SOURCE_REVISION", "unknown"),
                "workload_sha256": os.environ.get("LOAD_TEST_WORKLOAD_SHA256", "unknown"),
                "server_configuration": "not_attested; metrics describe runtime behavior"}
    RECORDER = RunRecorder(CONFIG.cases, CONFIG.max_requests, previews=CONFIG.preview_citations,
                           expect_parents=os.environ.get("LOAD_TEST_EXPECT_PARENTS", "false") == "true",
                           metadata=metadata)
    RECORDER.before = metrics_snapshot()


@events.test_stop.add_listener
def save_observations(environment, **kwargs):
    if RECORDER is None:
        return
    RECORDER.after = metrics_snapshot()
    CONFIG.observations_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG.observations_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(RECORDER.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(CONFIG.observations_path)
    # Locust's periodic CSV may omit the last requests when a capped run exits.
    # Export separate final CSVs via its public exporter after users have stopped.
    exporter = StatsCSV(environment, PERCENTILES_TO_REPORT)
    for filename, method in [("final_stats.csv", exporter.requests_csv), ("final_failures.csv", exporter.failures_csv)]:
        with (CONFIG.observations_path.parent / filename).open("w", newline="", encoding="utf-8") as target:
            method(csv.writer(target))


class EnterpriseKnowledgeQaUser(HttpUser):
    """使用真实 JWT 重复执行受租户治理保护的 QA 请求。"""

    host = CONFIG.base_url
    wait_time = between(
        CONFIG.wait_min_seconds,
        CONFIG.wait_max_seconds,
    )

    def on_start(self) -> None:
        self.headers = {
            "Authorization": (
                f"Bearer {CONFIG.access_token}"
            ),
            "Content-Type": "application/json",
        }

    @task
    def ask_knowledge_base(self) -> None:
        reservation = RECORDER.reserve() if RECORDER is not None else None
        if RECORDER is not None and reservation is None:
            raise StopUser()
        case = reservation[1] if reservation is not None else random.choice(CONFIG.cases)
        question = case.question
        valid_payload = None
        with self.client.post(
            "/api/qa",
            headers=self.headers,
            json={
                "knowledge_base_id": str(CONFIG.knowledge_base_id) if CONFIG.knowledge_base_id else None,
                "question": question,
            },
            name="/api/qa [pending]",
            timeout=CONFIG.request_timeout_seconds,
            catch_response=True,
        ) as response:
            try:
                payload = response.json()
            except ValueError:
                payload = None

            validation = validate_qa_response(
                status_code=response.status_code,
                payload=payload,
                request_id=response.headers.get(
                    "X-Request-ID"
                ),
                mode=CONFIG.mode,
                response_headers=response.headers,
            )
            response.request_meta["name"] = validation.name

            error = validation.message
            if RECORDER is not None and validation.successful and response.status_code == 200:
                error = current_response_error(payload)
            if RECORDER is not None and response.status_code == 429 and validation.name == "/api/qa [429]":
                error = "unclassified_throttle"
            successful = validation.successful and error is None
            if successful:
                response.success()
                if response.status_code == 200:
                    valid_payload = payload
            else:
                response.failure(
                    error
                    or "QA 响应校验失败"
                )
            if RECORDER is not None:
                RECORDER.record_qa(reservation[0], case, status=response.status_code,
                    successful=successful, elapsed_ms=response.request_meta["response_time"],
                    payload=payload, request_id=response.headers.get("X-Request-ID"),
                    status_label=validation.name.rsplit("[", 1)[-1].rstrip("]"))
        if RECORDER is not None and valid_payload and valid_payload["answerable"] and RECORDER.claim_preview(case.case_id):
            self.preview(case, valid_payload["citations"][0])
        if RECORDER is not None and len(RECORDER.qa) == CONFIG.max_requests:
            # Yield until every reserved preview finishes too; don't kill another user's GET.
            if len(RECORDER.previews) == len(RECORDER.preview_claims):
                gevent.spawn(self.environment.runner.quit)

    def preview(self, case, citation):
        path = "/api/knowledge-bases/{}/documents/{}/chunks/{}".format(
            quote(citation["knowledge_base_id"], safe=""), quote(citation["document_id"], safe=""),
            quote(citation["chunk_id"], safe=""))
        with self.client.get(path, headers=self.headers,
            params={"parent_id": citation["parent_id"]} if citation.get("parent_id") else {},
            name="/citation-preview [pending]", timeout=CONFIG.request_timeout_seconds,
            catch_response=True) as response:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            error = preview_error(response.status_code, payload, citation, response.headers)
            response.request_meta["name"] = f"/citation-preview [{response.status_code}]"
            if error is None:
                response.success()
            else:
                response.failure(error)
            RECORDER.previews.append({"case_id": case.case_id, "status_label": str(response.status_code),
                "successful": error is None, "elapsed_ms": response.request_meta["response_time"]})
