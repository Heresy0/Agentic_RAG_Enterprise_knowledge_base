"""Ordering gates and stage attribution, separate from legacy evidence coverage."""
import math

from .metrics import contains_fact, document_key, summarize_records


RANKING_VERSION = "2026-10-21-ranking-v1"
OBSERVED_STAGES = ("vector", "keyword", "fusion", "rerank_model", "rerank",
                   "parent_context", "agent_evidence", "authorized_final", "context", "citations")
TRANSITIONS = (("vector", "fusion"), ("keyword", "fusion"),
               ("fusion", "rerank_model"), ("rerank_model", "rerank"),
               ("rerank", "parent_context"), ("parent_context", "agent_evidence"),
               ("authorized_final", "context"), ("context", "citations"))


def graded_ranking(judgments, rows, *, k=5):
    """NDCG needs explicit graded judgments; unjudged hits never become grade zero."""
    if not judgments:
        return dict(ndcg_at_5=None, reason="no_graded_relevance_labels")
    if judgments.get("complete") is not True or not judgments.get("items"):
        return dict(ndcg_at_5=None, reason="incomplete_relevance_labels")
    labels = judgments["items"]
    for label in labels:
        if (type(label.get("grade")) is not int or not 0 <= label["grade"] <= 3
                or not label.get("document") or not label.get("text")):
            raise ValueError("Invalid graded relevance judgment")
    if len({(document_key(label["document"]), label["text"]) for label in labels}) != len(labels):
        raise ValueError("Duplicate graded relevance judgment")
    grades, used_labels = [], set()
    for row in rows[:k]:
        matches = [i for i, label in enumerate(labels)
                   if document_key(row.get("document_name", "")) == document_key(label["document"])
                   and contains_fact(row.get("content", ""), label["text"])]
        if not matches:
            return dict(ndcg_at_5=None, reason="unjudged_displayed_result")
        fresh = [i for i in matches if i not in used_labels]
        # Repeating one judged passage must not multiply its relevance gain.
        chosen = max(fresh, key=lambda i: labels[i]["grade"]) if fresh else None
        grades.append(labels[chosen]["grade"] if chosen is not None else 0)
        if chosen is not None:
            used_labels.add(chosen)
    def dcg(values):
        return sum((2 ** value - 1) / math.log2(index + 2) for index, value in enumerate(values))
    ideal = dcg(sorted((label["grade"] for label in labels), reverse=True)[:k])
    return dict(ndcg_at_5=dcg(grades) / ideal if ideal else None,
                top1_grade=grades[0] if grades else None,
                best_at_1=bool(grades) and grades[0] == max(label["grade"] for label in labels) if ideal else None,
                reason=None if ideal else "no_positive_relevance_labels")


def citation_rows(response, context):
    """Resolve only the actually displayed citations to their exact context rows."""
    rows = []
    for citation in response.get("citations", []):
        matches = [row for row in context if row.get("citation_id") == citation.get("citation_id")
                   and document_key(row.get("document_name", "")) == document_key(citation.get("document_name", ""))
                   and (not citation.get("chunk_id") or row.get("chunk_id") == citation["chunk_id"])
                   and (not citation.get("knowledge_base_id") or row.get("knowledge_base_id") == str(citation["knowledge_base_id"]))
                   and row.get("parent_id") == citation.get("parent_id")]
        # An unresolved citation still occupies its real display position.
        rows.append(matches[0] if len(matches) == 1 else dict(document_name="", content=""))
    return rows


def rank_diagnostics(metrics):
    changes = []
    for before, after in TRANSITIONS:
        left, right = metrics.get(before, {}), metrics.get(after, {})
        if not left.get("scored") or not right.get("scored"):
            continue
        if left.get("matched_evidence_set", 0) != right.get("matched_evidence_set", 0):
            continue  # Different approved alternatives are not evidence loss.
        lost = sorted(set(left.get("evidence_ranks", {})) - set(right.get("evidence_ranks", {})))
        a, b = left.get("first_relevant_rank"), right.get("first_relevant_rank")
        if lost or (a is not None and (b is None or b > a)):
            changes.append(dict(before=before, after=after, lost_evidence_indices=lost,
                                first_rank_before=a, first_rank_after=b))
    return changes


def summarize_ranking(records):
    result = {}
    for stage in OBSERVED_STAGES:
        scored = [row.get("metrics", {}).get(stage, {}) for row in records]
        scored = [row for row in scored if row.get("scored")]
        ndcg = [row["graded_ranking"]["ndcg_at_5"] for row in scored
                if row.get("graded_ranking", {}).get("ndcg_at_5") is not None]
        result[stage] = dict(scored_cases=len(scored),
            **{f"hit_at_{k}": sum(row.get("first_relevant_rank") is not None and
                                    row["first_relevant_rank"] <= k for row in scored) / len(scored)
                                    if scored else None for k in (1, 3, 5)},
            mrr=sum(row["reciprocal_rank"] for row in scored) / len(scored) if scored else None,
            ndcg_at_5=sum(ndcg) / len(ndcg) if ndcg else None, ndcg_scored_cases=len(ndcg))
    return result


def ranking_layer(quality):
    records = []
    for record in quality.get("records", []):
        metrics = record.get("metrics", {})
        rules = metrics.get("ranking_requirements", {"max_first_relevant_rank": 1})
        maximum = rules.get("max_first_relevant_rank", 1)
        if type(maximum) is not int or maximum < 1:
            raise ValueError("max_first_relevant_rank must be a positive integer")
        checks = []
        for stage in ("rerank", "context"):
            metric = metrics.get(stage, {})
            if metric.get("reason") == "no_positive_evidence_labels":
                state = "not_applicable"
            elif not metric.get("scored"):
                state = "skipped"
            else:
                rank = metric.get("first_relevant_rank")
                state = "passed" if rank is not None and rank <= maximum and not metric.get("incomplete_reason") else "failed"
            graded = metric.get("graded_ranking", {})
            if state == "passed" and graded.get("best_at_1") is False:
                state = "failed"
            elif state == "passed" and graded.get("reason") not in (None, "no_graded_relevance_labels"):
                state = "partial"
            checks.append(dict(stage=stage, status=state, max_first_relevant_rank=maximum,
                               best_at_1=graded.get("best_at_1"),
                               ndcg_at_5=graded.get("ndcg_at_5"),
                               first_relevant_rank=metric.get("first_relevant_rank")))
        if "citations" in metrics and (record.get("response") or {}).get("answerable"):
            metric = metrics["citations"]
            if metric.get("scored"):
                rank = metric.get("first_relevant_rank")
                checks.append(dict(stage="citations", status="passed" if rank == 1 else "failed",
                                   first_relevant_rank=rank, max_first_relevant_rank=1))
        states = [row["status"] for row in checks]
        state = ("error" if record["status"] == "error" or record.get("scope_violation_count") else
                 "failed" if "failed" in states else "skipped" if "skipped" in states else "partial" if "partial" in states else
                 "not_applicable" if all(s == "not_applicable" for s in states) else "passed")
        records.append(dict(id=record["id"], status=state, checks=checks,
                            diagnostics=metrics.get("rank_diagnostics", []),
                            model_usage=metrics.get("model_usage")))
    return dict(status="completed", evaluation_type=quality.get("evaluation_type"),
                summary=summarize_records(records), records=records, version=RANKING_VERSION,
                stages=summarize_ranking(quality.get("records", [])),
                limitation="Top-1 checks annotated supporting evidence, not the unique best answer. "
                           "NDCG requires explicit graded labels. Citation order is checked only when observed.")
