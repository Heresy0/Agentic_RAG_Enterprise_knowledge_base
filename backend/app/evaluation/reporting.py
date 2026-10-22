"""Unified reports explicitly distinguish untested stages from passing checks."""
import json
from datetime import datetime, timezone
from .metrics import summarize_generation
from .ranking import ranking_layer
from .semantics import semantics_layer
from ..model_usage import summarize_usage


LAYERS = ("parsing", "index", "retrieval", "rerank", "context", "answer", "ranking", "semantics", "conversations", "security", "performance")


def skipped(reason):
    return dict(status="skipped", reason=reason)


def stage_report(quality, stage):
    records = []
    for record in quality.get("records", []):
        metric = record.get("metrics", {}).get(stage)
        if record["status"] == "error":
            records.append(dict(id=record["id"], status="error", error_type=record.get("error_type")))
        elif metric is not None and metric.get("reason") == "no_positive_evidence_labels":
            records.append(dict(id=record["id"], status="not_applicable", reason="negative_case_has_no_positive_retrieval_labels"))
        elif metric is None or not metric.get("scored"):
            records.append(dict(id=record["id"], status="skipped", reason="no_observation_or_positive_labels"))
        else:
            records.append(dict(id=record["id"], status="passed" if metric["all_evidence_present"] else "failed", metrics=metric))
        records[-1]["model_usage"] = record.get("metrics", {}).get("model_usage")
    from .metrics import summarize_records
    return dict(status="completed", summary=summarize_records(records), records=records,
                metrics=quality.get("stages", {}).get(stage), evaluation_type=quality.get("evaluation_type"))


def quality_layers(quality):
    answer_records = []
    for record in quality.get("records", []):
        metric = record.get("metrics", {}).get("answer")
        status = "error" if record["status"] == "error" else "skipped" if metric is None else "passed" if metric["automatic_proxy_pass"] else "failed"
        answer_records.append(dict(id=record["id"], status=status, metrics=metric,
                                   generation=record.get("metrics", {}).get("generation"),
                                   model_usage=record.get("metrics", {}).get("model_usage"),
                                   response=record.get("response"), error_type=record.get("error_type")))
    from .metrics import summarize_records
    return dict(
        retrieval=dict(status="completed", stages={key: quality.get("stages", {}).get(key) for key in ("vector", "keyword", "fusion")},
                       **{key: value for key, value in stage_report(quality, "fusion").items() if key != "status"}),
        rerank=stage_report(quality, "rerank"), context=stage_report(quality, "context"),
        ranking=ranking_layer(quality), semantics=semantics_layer(quality),
        answer=dict(status="completed", summary=summarize_records(answer_records), records=answer_records,
                    generation_metrics=summarize_generation(quality.get("records", [])),
                    limitation="Phrase/source proxies; semantic correctness and faithfulness require human review."))


def outcome(report):
    incomplete = report.get("mode") == "replay"
    for layer in report["layers"].values():
        summary = layer.get("summary", {})
        if layer["status"] in ("failed", "error") or summary.get("failed", 0) or summary.get("error", 0):
            return "failed"
        if (layer["status"] in ("skipped", "partial") or summary.get("skipped", 0)
                or summary.get("partial", 0) or layer.get("evaluation_type", "").startswith("replay")):
            incomplete = True
        if layer.get("status") == "imported" and layer.get("report", {}).get("passed") is not True:
            return "failed"
    return "incomplete" if incomplete else "passed_automated_checks_not_production_certification"


def compare_baseline(report, baseline):
    """Only compare like-for-like runs; never turn historical Chroma numbers into current gates."""
    keys = ("schema_version", "scorer_version", "scoring_rules_sha256", "evaluator_version", "quality_policy_sha256", "mode", "dataset_sha256", "selected_case_ids", "requested_conversation_ids",
            "conversation_backend", "conversations_sha256", "corpus_manifest_sha256", "scope_config_sha256",
            "suite_sha256", "selected_groups")
    mismatches = [key for key in keys if report.get(key) != baseline.get(key)]
    if set(report["layers"]) != set(baseline.get("layers", {})):
        mismatches.append("selected_layers")
    if mismatches:
        return dict(status="incompatible", mismatches=mismatches)
    changes = []
    for name, layer in report["layers"].items():
        previous = baseline["layers"][name]
        metrics = layer.get("metrics", {}) or {}
        for metric in ("hit_rate", "hit_at_1", "hit_at_3", "hit_at_5", "mrr", "mean_evidence_recall", "all_evidence_rate"):
            current, old = metrics.get(metric), (previous.get("metrics", {}) or {}).get(metric)
            if isinstance(current, (int, float)) and isinstance(old, (int, float)):
                changes.append(dict(layer=name, metric=metric, baseline=old, current=current, delta=current - old))
        for metric in ("pass_rate",):
            current, old = layer.get("summary", {}).get(metric), previous.get("summary", {}).get(metric)
            if isinstance(current, (int, float)) and isinstance(old, (int, float)):
                changes.append(dict(layer=name, metric=metric, baseline=old, current=current, delta=current - old))
    return dict(status="compared", changes=changes, limitation="Deltas are diagnostic, not an automatic deployment gate.")


def write_report(report, directory):
    report["created_at"] = datetime.now(timezone.utc).isoformat()
    report["overall_status"] = outcome(report)
    # Quality layers share the SAME call trace. Aggregate answer records only once.
    quality_records = next((report["layers"][name].get("records", [])
                            for name in ("answer", "retrieval", "rerank", "context", "ranking", "semantics")
                            if report["layers"].get(name, {}).get("records")), [])
    usage_rows = [row.get("model_usage") for row in quality_records]
    usage_rows.extend(turn.get("metrics", {}).get("model_usage")
                      for row in report["layers"].get("conversations", {}).get("records", []) for turn in row.get("turns", []))
    observed = [row for row in usage_rows if row and row.get("observed")]
    events = [event for row in observed for event in row.get("events", [])]
    report["model_usage"] = dict(**summarize_usage(events, observed=bool(observed)),
                                observed_requests=len(observed), unobserved_requests=len(usage_rows) - len(observed),
                                limitation="Known usage subtotal only; unknown usage is not zero. Historical replay is not new spend.")
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 企业知识库分层评估报告", "", "运行模式：" + report["mode"], "",
             "结论：" + report["overall_status"], "", "passed仅表示自动检查通过，不代表语义评审通过或生产安全认证。", "",
             "| 层 | 执行状态 | 通过 | 失败 | 错误 | 未测试/部分 | 不适用 |", "|---|---|---:|---:|---:|---:|---:|"]
    for name, layer in report["layers"].items():
        summary = layer.get("summary", {})
        lines.append(f"| {name} | {layer['status']} | {summary.get('passed', '—')} | {summary.get('failed', '—')} | {summary.get('error', '—')} | {summary.get('skipped', 0) + summary.get('partial', 0) if summary else '—'} | {summary.get('not_applicable', 0) if summary else '—'} |")
    if report.get('group_scores'):
        lines.extend(['','## 单轮分组评分','',
            '各组使用原事实标准；历史100题成绩只属于main组，缺失观察不计为通过。','',
            '| 分组 | 层 | 已选题数 | 通过 | 失败 | 错误 | 未观察/部分 | 不适用 | Hit Rate | Hit@1 | Hit@3 | MRR | 全证据完整率 |',
            '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|'])
        for group in report['group_scores'].values():
            for name,layer in group['layers'].items():
                s=layer['summary'];m=layer.get('metrics') or {}
                lines.append(f"| {group['name']} | {name} | {group['selected_cases']} | {s['passed']} | {s['failed']} | {s['error']} | {s['skipped']+s['partial']} | {s['not_applicable']} | {m.get('hit_rate','—')} | {m.get('hit_at_1','—')} | {m.get('hit_at_3','—')} | {m.get('mrr','—')} | {m.get('all_evidence_rate','—')} |")
    for name, layer in report["layers"].items():
        lines.extend(["", "## " + name, ""])
        for key in ("reason", "limitation", "evaluation_type"):
            if layer.get(key):
                lines.extend([f"{key}：{layer[key]}", ""])
        metrics = layer.get("metrics") or layer.get("stages")
        if metrics:
            lines.extend(["```json", json.dumps(metrics, ensure_ascii=False, indent=2), "```", ""])
        generation = layer.get("generation_metrics")
        if generation is not None:
            lines.extend(["首次生成与补全诊断（不等于语义准确率）：", "",
                          "```json", json.dumps(generation, ensure_ascii=False, indent=2), "```", ""])
            rows = (layer.get("records", []) if name == "answer" else
                    [turn for record in layer.get("records", []) for turn in record.get("turns", [])])
            for row in rows:
                metric = row.get("generation") if name == "answer" else row.get("metrics", {}).get("generation")
                if metric and metric.get("observed") and not metric["first_pass"]["automatic_proxy_pass"]:
                    lines.append(f"- {row['id']} 首次生成未通过代理检查；缺失事实：{metric['first_pass']['missing_facts']}；最终结果：{row['status']}")
            lines.append("")
        flagged = [row for row in layer.get("records", []) if row["status"] not in ("passed", "not_applicable")]
        if flagged:
            lines.extend(["非通过项（完整结果见report.json）：", ""])
            lines.extend(f"- {row['id']}：{row['status']}；{row.get('reason', row.get('error_type', '请核对指标和原始结果'))}" for row in flagged)
    lines.extend(["", "## 模型 Token 与成本审计", "", "```json",
                  json.dumps(report["model_usage"], ensure_ascii=False, indent=2), "```", "",
                  "reported_total_tokens 是已知用量的小计。未知不等于零；无定价时费用为空；不同币种分别统计。", "",
                  "成本按运行时价格快照估算，不等于供应商账单；重放仅展示历史用量，不产生新费用。"])
    lines.extend(["", "## 解读限制", "",
                  "- 未测试不能算通过；不同范围、身份、语料版本的分数不可直接比较。",
                  "- 关键词匹配不能识别所有否定、错误归因或同义表达，需人工抽检。",
                  "- 端到端事实正确性、忠实上下文、引用支持是不同指标。",
                  "- 报告可能含业务答案；原始trace包含授权文档片段，请按内部敏感资料保管，不上传知识库。", ""])
    if report.get("baseline_comparison"):
        lines.extend(["## 同口径基线对比", "", "```json", json.dumps(report["baseline_comparison"], ensure_ascii=False, indent=2), "```", ""])
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")
