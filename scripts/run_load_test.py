"""Run a bounded single-process HTTP benchmark against the configured API."""
import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.load_test_support import load_cases, LoadTestThresholds, build_load_test_report, LoadTestConfig

PROFILES = {"simple": {"simple"}, "complex": {"complex"}, "mixed": {"simple", "complex", "refusal"}, "governance": {"simple"}}


def remaining_token_seconds(token, now=None):
    """Expiry preflight only; the protected API verifies JWT signatures and claims."""
    try:
        part = token.removeprefix("Bearer ").split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        expires = claims["exp"]
        if type(expires) not in {int, float} or not math.isfinite(expires):
            raise ValueError()
        return expires - (time.time() if now is None else now)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ValueError("无法读取 JWT 有效期；请更新 .env.evaluation 的 Access Token") from exc


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default="mixed")
    parser.add_argument("--workload", type=Path, default=ROOT / "evals/current/load_workload.jsonl")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, default=ROOT / ".env.evaluation")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--knowledge-base-id", help="Omit for all authorized knowledge bases")
    parser.add_argument("--users", type=int, default=2)
    parser.add_argument("--max-requests", type=int, default=24)
    parser.add_argument("--run-seconds", type=int, default=120)
    parser.add_argument("--request-timeout", type=int, default=90)
    parser.add_argument("--max-p95-ms", type=float, default=15000)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--child-only", action="store_true", help="Do not require parent IDs; does not change server configuration")
    parser.add_argument("--allow-model-calls", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Validate workload and limits without network or tokens")
    args = parser.parse_args(argv)
    if not 1 <= args.users <= 16 or not 1 <= args.max_requests <= 1000 or not 1 <= args.run_seconds <= 600 or not 1 <= args.request_timeout <= 120:
        parser.error("用户数1..16、请求数1..1000、运行秒数1..600、请求超时1..120")
    if not args.dry_run and not args.allow_model_calls:
        parser.error("真实压测必须显式指定 --allow-model-calls")
    if args.max_p95_ms <= 0 or args.max_p95_ms != args.max_p95_ms or args.max_p95_ms == float("inf"):
        parser.error("P95 门槛必须为有限正数")
    return args


def run(args):
    cases = load_cases(args.workload, groups=frozenset(PROFILES[args.profile]))
    if args.max_requests < len(cases):
        raise ValueError("请求上限小于选中题目数量，无法覆盖负载")
    if args.profile == "governance" and args.preview:
        raise ValueError("治理压测不混入引用预览；请在 steady 场景验证预览")
    if args.dry_run:
        print(json.dumps({"profile": args.profile, "cases": [c.case_id for c in cases],
                          "max_requests": args.max_requests, "users": args.users, "model_calls_started": False}))
        return 0
    out = args.output_dir.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("输出目录非空，请使用新目录保留每次原始结果")
    token = (dotenv_values(args.token_file).get("ENTERPRISE_KB_ACCESS_TOKEN") or "").strip()
    token = token.removeprefix("Bearer ").strip()
    required_seconds = args.run_seconds + args.request_timeout + 30
    if remaining_token_seconds(token) < required_seconds:
        raise ValueError(f"JWT 剩余有效期不足 {required_seconds} 秒；请重新登录并保存令牌")
    env = os.environ.copy()
    env.update({"ENTERPRISE_KB_ACCESS_TOKEN": token, "LOAD_TEST_BASE_URL": args.base_url,
                "LOAD_TEST_SCOPE": "single" if args.knowledge_base_id else "all",
                "LOAD_TEST_KNOWLEDGE_BASE_ID": args.knowledge_base_id or "",
                "LOAD_TEST_QUESTIONS_FILE": str(args.workload.resolve()), "LOAD_TEST_CATEGORIES": "",
                "LOAD_TEST_GROUPS": ",".join(sorted(PROFILES[args.profile])),
                "LOAD_TEST_MAX_REQUESTS": str(args.max_requests), "LOAD_TEST_PROFILE": args.profile,
                "LOAD_TEST_MODE": "governance" if args.profile == "governance" else "steady",
                "LOAD_TEST_PREVIEW_CITATIONS": str(args.preview).lower(),
                "LOAD_TEST_EXPECT_PARENTS": str(not args.child_only).lower(),
                "LOAD_TEST_OBSERVATIONS_FILE": str(out / "observations.json"),
                "LOAD_TEST_REQUEST_TIMEOUT_SECONDS": str(args.request_timeout),
                "LOAD_TEST_WAIT_MIN_SECONDS": "0.1" if args.profile == "governance" else "0.5",
                "LOAD_TEST_WAIT_MAX_SECONDS": "0.2" if args.profile == "governance" else "1.5",
                "LOAD_TEST_WORKLOAD_SHA256": hashlib.sha256(args.workload.read_bytes()).hexdigest(),
                "LOAD_TEST_SOURCE_REVISION": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
                "PYTHONIOENCODING": "utf-8"})
    config = LoadTestConfig.from_environment(env, project_root=ROOT)
    health = requests.get(config.base_url + "/health/ready", timeout=10, allow_redirects=False)
    if health.status_code != 200 or health.json().get("status") != "ok":
        raise ValueError("API 未就绪，未启动压测")
    scope = requests.get(config.base_url + "/api/knowledge-bases", timeout=10, allow_redirects=False,
                         headers={"Authorization": f"Bearer {token}"})
    if scope.status_code != 200:
        raise ValueError(f"测试账号认证/授权预检失败：HTTP {scope.status_code}，未启动模型调用")
    accessible = scope.json().get("items", [])
    if args.knowledge_base_id:
        if args.knowledge_base_id not in {item["id"] for item in accessible}:
            raise ValueError("目标知识库不在测试账号授权范围")
    elif len(accessible) < 4:
        raise ValueError("默认负载需要四个知识库读取权限；请使用 Alice 或指定匹配的单库负载")
    out.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "locust", "-f", "tests/load/locustfile.py", "--headless",
               "-u", str(args.users), "-r", str(args.users if args.profile == "governance" else 1),
               "-t", f"{args.run_seconds}s", "--stop-timeout", str(args.request_timeout),
               "--csv", str(out / "qa"), "--csv-full-history", "--html", str(out / "qa.html"),
               "--only-summary"]
    with (out / "locust.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    observations = out / "observations.json"
    if not observations.is_file() or not (out / "final_stats.csv").is_file():
        raise ValueError("Locust 未产生完整产物，请检查本地日志；本次不能记为通过")
    thresholds = LoadTestThresholds(min_requests=args.max_requests,
        min_successful_requests=1 if args.profile == "governance" else args.max_requests,
        max_failure_rate=0, max_p95_ms=args.max_p95_ms, max_server_errors=0,
        min_throttled_requests=1 if args.profile == "governance" else 0)
    report = build_load_test_report(stats_path=out / "final_stats.csv", failures_path=out / "final_failures.csv",
                                   observations_path=observations, thresholds=thresholds, scenario=config.mode)
    report["locust_exit_code"] = result.returncode
    report["checks"]["locust_completed"] = result.returncode == 0
    report["passed"] = all(report["checks"].values())
    report["workload_definition"] = {"profile": args.profile, "case_ids": [c.case_id for c in cases],
                                    "sha256": env["LOAD_TEST_WORKLOAD_SHA256"], "users": args.users}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "profile": args.profile, "summary": report["summary"],
                      "checks": report["checks"], "report": str(out / "report.json")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


def main(argv=None):
    try:
        return run(parse_args(argv))
    except (ValueError, OSError, requests.RequestException) as exc:
        # Do not print arbitrary requests exceptions which can include URLs or headers.
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(json.dumps({"started_or_complete": False, "error": message}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
