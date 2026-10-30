"""Fixed-cutoff metrics over labelled required evidence, separate from r13 scoring."""
from .metrics import evidence_metrics as legacy_evidence_metrics
from .metrics import summarize_stages as legacy_summarize_stages


CUTOFFS = (1, 3, 5, 8)


def evidence_metrics(evidence, rows, *, alternative_sets=()):
    result = legacy_evidence_metrics(evidence, rows, alternative_sets=alternative_sets)
    if result.get("scored"):
        result["at_k"] = {}
        for k in CUTOFFS:
            # Choose an accepted evidence set within this cutoff, not over the full list.
            bounded = legacy_evidence_metrics(evidence, rows[:k], alternative_sets=alternative_sets)
            result["at_k"][str(k)] = {name: bounded[name] for name in
                ("hit", "reciprocal_rank", "evidence_recall", "all_evidence_present")}
    return result


def summarize_stages(records):
    stages = legacy_summarize_stages(records)
    for stage, summary in stages.items():
        summary["at_k"] = {}
        for k in CUTOFFS:
            observed = [row["metrics"][stage]["at_k"][str(k)] for row in records
                        if row.get("status") != "error"
                        and str(k) in row.get("metrics", {}).get(stage, {}).get("at_k", {})]
            count = len(observed)
            summary["at_k"][str(k)] = dict(scored_cases=count, **{
                name: sum(row[source] for row in observed) / count if count else None
                for name, source in (("hit_rate", "hit"), ("mrr", "reciprocal_rank"),
                                     ("evidence_recall", "evidence_recall"),
                                     ("all_evidence_rate", "all_evidence_present"))})
    return stages
