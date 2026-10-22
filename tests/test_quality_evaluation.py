"""Quality regression: a recovered keyword is not necessarily a good delivered answer."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from langchain_core.documents import Document

from backend.app.evaluation.metrics import evidence_metrics, score_answer
from backend.app.evaluation.quality_policy import apply_quality_policy
from backend.app.evaluation.ranking import graded_ranking, ranking_layer
from backend.app.evaluation.reporting import outcome, quality_layers, write_report, compare_baseline
from backend.app.evaluation.runner import score_trace, replay
from backend.app.evaluation.scoring_rules import apply_scoring_rules
from backend.app.evaluation.semantics import apply_semantic_reviews, score_semantics
from backend.app.evaluation.trace import capture_trace, record_documents, record_scope
from backend.app.security.retrieval_scope import RetrievalScope
from scripts.evaluate_system import main, parse_args
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]


def case():
    return dict(id="ordering", question="首次响应时限是多少？", reference_answer="首次响应为8分钟", answerable=True,
                required_facts=["8分钟"], expected_evidence=[dict(document="direct.md", text="首次响应为8分钟")])


def relevant():
    return dict(document_name="direct.md", content="首次响应为8分钟", citation_id="资料1", chunk_id="direct")


def noise():
    return dict(document_name="background.md", content="日志应保留30天", citation_id="资料2", chunk_id="background")


def quality(stages, *, response=None):
    trace = dict(stages=stages)
    if response is not None:
        trace["response"] = response
    return dict(records=[score_trace(case(), trace, include_answer=response is not None)])


def test_first_result_is_a_gate_even_when_all_evidence_is_recovered():
    rows = [noise()] * 9 + [relevant()]
    q = quality(dict(rerank=rows, context=rows))
    assert quality_layers(q)["rerank"]["records"][0]["status"] == "passed"  # unchanged legacy coverage
    ranking = quality_layers(q)["ranking"]
    assert ranking["summary"]["failed"] == 1
    metric = q["records"][0]["metrics"]["rerank"]
    assert metric["reciprocal_rank"] == .1 and not metric["hit_at_1"] and not metric["hit_at_3"]
    assert outcome(dict(mode="live", layers=dict(ranking=ranking))) == "failed"


@pytest.mark.parametrize("name,before,after", [
    ("rrf_discards_keyword_top1", "keyword", "fusion"),
    ("topic_preference_overrides_model", "rerank_model", "rerank"),
    ("metadata_paraphrase_promotes_body", "rerank_model", "rerank"),
    ("parent_dedup_leaves_missing_source", "rerank", "parent_context"),
    ("agent_keeps_initial_background_first", "parent_context", "agent_evidence"),
    ("long_evidence_skipped_for_short_background", "authorized_final", "context"),
])
def test_audited_pipeline_regressions_are_attributed(name, before, after):
    # Frozen minimal observations of the audited failures, not assertions that
    # production retrieval should continue to behave incorrectly.
    q = quality({before: [relevant(), noise()], after: [noise()]})
    changes = q["records"][0]["metrics"]["rank_diagnostics"]
    assert any(row["before"] == before and row["after"] == after
               and row["lost_evidence_indices"] == ["0"] for row in changes), name


def test_displayed_citation_order_is_checked_separately_from_context():
    response = dict(answerable=True, answer="先讲背景[资料2]。首次响应为8分钟[资料1]。",
                    citations=[{key: row[key] for key in ("document_name", "citation_id", "chunk_id")}
                               for row in (noise(), relevant())])
    q = quality(dict(rerank=[relevant(), noise()], context=[relevant(), noise()]), response=response)
    metric = q["records"][0]["metrics"]
    assert metric["context"]["hit_at_1"] and not metric["citations"]["hit_at_1"]
    assert ranking_layer(q)["summary"]["failed"] == 1
    assert any(row["after"] == "citations" for row in metric["rank_diagnostics"])
    forged = deepcopy(response)
    forged["citations"][0] = dict(relevant(), chunk_id="forged")
    assert not quality(dict(context=[relevant()]), response=forged)["records"][0]["metrics"]["citations"]["hit_at_1"]


def test_unobserved_and_negative_ranking_are_not_fake_passes():
    assert ranking_layer(quality({}))["summary"]["skipped"] == 1
    record = score_trace(dict(case(), answerable=False, expected_evidence=[]), dict(stages=dict(rerank=[], context=[])))
    assert ranking_layer(dict(records=[record]))["summary"]["not_applicable"] == 1
    q = quality(dict(rerank=[relevant()], context=[]))
    assert ranking_layer(q)["summary"]["failed"] == 1


def test_ndcg_requires_graded_labels_and_detects_reversed_order():
    labels = dict(complete=True, items=[dict(document="direct.md", text="首次响应为8分钟", grade=3),
                                       dict(document="background.md", text="日志应保留30天", grade=0)])
    assert graded_ranking(labels, [relevant(), noise()])["ndcg_at_5"] == 1
    assert graded_ranking(labels, [noise(), relevant()])["ndcg_at_5"] < 1
    assert graded_ranking(labels, [relevant(), relevant()])["ndcg_at_5"] == 1
    assert graded_ranking(None, [relevant()])["ndcg_at_5"] is None
    assert graded_ranking(labels, [dict(document_name="unknown", content="unknown")])["ndcg_at_5"] is None
    with pytest.raises(ValueError):
        graded_ranking(dict(complete=True, items=[dict(document="x", text="x", grade=True)]), [])


def test_graded_top1_gate_distinguishes_best_from_merely_relevant():
    q = quality(dict(rerank=[relevant()], context=[relevant()]))
    for stage in ("rerank", "context"):
        q["records"][0]["metrics"][stage]["graded_ranking"] = dict(best_at_1=False)
    assert ranking_layer(q)["summary"]["failed"] == 1


def mh001():
    directory = ROOT / "evals/current"
    raw = [json.loads(line) for line in (directory / "single_turn.jsonl").read_text(encoding="utf-8").splitlines()]
    scored = apply_scoring_rules(raw, directory / "scoring_rules.json")
    augmented, _ = apply_quality_policy(scored, directory / "quality_policy.json", directory / "single_turn.jsonl")
    c = next(row for row in augmented if row["id"] == "MH-001")
    context = [dict(document_name=e["document"], content=e["text"], citation_id=f"资料{i}", chunk_id=f"c{i}")
               for i, e in enumerate(c["expected_evidence"], 1)]
    return c, context


def semantic_response(context, answer):
    return dict(answerable=True, answer=answer,
                citations=[{key: row[key] for key in ("document_name", "citation_id", "chunk_id")} for row in context])


def test_reversed_semantics_cannot_hide_behind_full_keyword_coverage():
    c, context = mh001()
    r = semantic_response(context, "该项目不需要连续2轮校验，缺失数不必为0，附件索引不必一致，即使D1或D2未关闭也允许验收。[资料1][资料2]")
    assert score_answer(c, r, context)["automatic_proxy_pass"]  # preserve and expose the historical proxy blind spot
    metric = score_semantics(c, r, context)
    assert metric["status"] == "failed"
    assert any(row["id"] == "reversed_acceptance_conditions" and row["status"] == "failed"
               for row in metric["bounded_checks"])


@pytest.mark.parametrize("replacement", [
    ("连续2轮", "连续1轮"), ("记录缺失数为0", "记录缺失数为1"),
    ("附件索引比对一致", "附件索引比对不必一致"),
    ("不得有未关闭的D1或D2缺陷", "D1或D2未关闭也允许验收"),
])
def test_condition_mutations_fail_even_with_correct_sources(replacement):
    c, context = mh001()
    assert replacement[0] in c["reference_answer"]
    answer = c["reference_answer"].replace(*replacement) + "[资料1][资料2]"
    assert score_semantics(c, semantic_response(context, answer), context)["status"] == "failed"


def test_correct_assertions_still_require_independent_review_and_correct_source_binding():
    c, context = mh001()
    r = semantic_response(context, c["reference_answer"] + "[资料1][资料2]")
    metric = score_semantics(c, r, context)
    assert metric["bounded_checks_passed"] and metric["status"] == "partial"
    r["answer"] = c["reference_answer"] + "[资料1]"
    assert score_semantics(c, r, context)["status"] == "failed"
    plain = score_semantics(case(), dict(answerable=True, answer="首次响应为8分钟", citations=[]), [])
    assert plain["status"] == "skipped" and plain["semantic_correctness"] == "requires_independent_review"


def reviewed_quality():
    c, context = mh001()
    r = semantic_response(context, c["reference_answer"] + "[资料1][资料2]")
    record = score_trace(c, dict(stages=dict(rerank=context, context=context), response=r), include_answer=True)
    return dict(records=[record]), record["metrics"]["semantics"]["review_binding"]


def test_review_is_bound_to_exact_question_answer_context_and_criteria(tmp_path):
    q, binding = reviewed_quality()
    path = tmp_path / "reviews.jsonl"
    review = dict(id="MH-001", binding=binding, verdict="passed", reviewer="independent-reviewer", reason="核对全部准入条件及逐结论来源")
    path.write_text(json.dumps(review), encoding="utf-8")
    updated = apply_semantic_reviews(q, path)
    assert quality_layers(updated)["semantics"]["summary"]["passed"] == 1
    assert q["records"][0]["metrics"]["semantics"]["status"] == "partial"
    for key in binding:
        stale = deepcopy(review)
        stale["binding"][key] = "stale"
        path.write_text(json.dumps(stale), encoding="utf-8")
        with pytest.raises(ValueError, match="fingerprint"):
            apply_semantic_reviews(q, path)
    path.write_text(json.dumps(review) + "\n" + json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        apply_semantic_reviews(q, path)


def test_uncertain_review_is_incomplete_and_cannot_override_failed_assertions(tmp_path):
    q, binding = reviewed_quality()
    path = tmp_path / "reviews.jsonl"
    review = dict(id="MH-001", binding=binding, verdict="uncertain", reviewer="reviewer", reason="条件有歧义")
    path.write_text(json.dumps(review), encoding="utf-8")
    layer = quality_layers(apply_semantic_reviews(q, path))["semantics"]
    assert outcome(dict(mode="live", layers=dict(semantics=layer))) == "incomplete"
    q["records"][0]["metrics"]["semantics"]["bounded_checks"][0]["status"] = "failed"
    path.write_text(json.dumps(dict(review, verdict="passed")), encoding="utf-8")
    assert quality_layers(apply_semantic_reviews(q, path))["semantics"]["summary"]["failed"] == 1


def test_raw_model_rank_and_scores_survive_opt_in_trace():
    scope = RetrievalScope(uuid4(), uuid4())
    document = Document(page_content="safe", metadata=dict(tenant_id=str(scope.tenant_id),
        knowledge_base_id=str(scope.knowledge_base_id), model_rank=1, final_rank=7, rerank_score=.99, rrf_rank=2))
    with capture_trace() as trace:
        record_scope("q", scope)
        record_documents("rerank_model", [document])
    row = trace.to_dict()["stages"]["rerank_model"][0]
    assert (row["model_rank"], row["final_rank"], row["rerank_score"], row["rrf_rank"]) == (1, 7, .99, 2)


def test_model_snapshot_precedes_policy_override_without_changing_runtime_order():
    from langchain_core.retrievers import BaseRetriever
    from backend.app.knowledge.rerank_retriever import RerankRetriever
    from backend.app.knowledge.reranker import BaseReranker, RerankResult
    class Static(BaseRetriever):
        documents: list[Document]
        def _get_relevant_documents(self, query, *, run_manager):
            return self.documents
    class Ranked(BaseReranker):
        def rerank(self, **kwargs):
            return [RerankResult(0, .99), RerankResult(1, .1)]
    scope = RetrievalScope(uuid4(), uuid4())
    docs = [Document(id=str(i), page_content=f"content{i}", metadata=dict(tenant_id=str(scope.tenant_id),
            knowledge_base_id=str(scope.knowledge_base_id))) for i in range(2)]
    retriever = RerankRetriever(base_retriever=Static(documents=docs), reranker=Ranked(), top_n=2,
                               result_processor=lambda question, items: list(reversed(items)))
    with capture_trace() as trace:
        record_scope("q", scope)
        result = retriever.invoke("q")
    raw = trace.to_dict()["stages"]["rerank_model"]
    assert [row["chunk_id"] for row in raw] == ["0", "1"]
    assert [row.id for row in result] == ["1", "0"]
    assert result[1].metadata["model_rank"] == 1 and result[1].metadata["final_rank"] == 2


def test_budget_exclusion_trace_identifies_the_skipped_evidence():
    from backend.app.knowledge.context_builder import ContextBuilder
    first = Document(id="first", page_content="short first", metadata=dict(source="a"))
    large = Document(id="large", page_content="long" * 200, metadata=dict(source="b"))
    last = Document(id="last", page_content="short last", metadata=dict(source="c"))
    budget = len(ContextBuilder().build([first]).text) + len(ContextBuilder().build([last]).text) + 2
    with capture_trace() as trace:
        context = ContextBuilder(max_characters=budget).build([first, large, last])
    assert [item.chunk_id for item in context.items] == ["first", "last"]
    exclusions = trace.to_dict()["stages"]["context_selection"][0]["exclusions"]
    assert dict(chunk_id="large", reason="whole_block_exceeds_remaining_budget") in exclusions


def test_new_layers_are_opt_in_for_paid_calls_and_have_cli_failure_exit(tmp_path):
    for layer in ("ranking", "semantics"):
        with pytest.raises(SystemExit):
            parse_args(["--mode", "live", "--layers", layer])
    trace = dict(id=case()["id"], stages=dict(rerank=[noise(), relevant()], context=[relevant()]))
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "single_turn.jsonl").write_text(json.dumps(case()) + "\n", encoding="utf-8")
    path = tmp_path / "trace.jsonl"
    path.write_text(json.dumps(trace) + "\n", encoding="utf-8")
    out = tmp_path / "report"
    assert main(["--mode", "replay", "--layers", "ranking", "--dataset-dir", str(dataset),
                 "--trace-input", str(path), "--output-dir", str(out)]) == 1
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["layers"]["ranking"]["summary"]["failed"] == 1
    assert report["live_model_calls_enabled"] is False


def test_policy_does_not_change_gold_and_incompatible_baselines_are_rejected(tmp_path):
    directory = ROOT / "evals/current"
    raw = [json.loads(line) for line in (directory / "single_turn.jsonl").read_text(encoding="utf-8").splitlines()]
    original = deepcopy(raw)
    augmented, sha = apply_quality_policy(raw, directory / "quality_policy.json", directory / "single_turn.jsonl")
    assert raw == original and sha
    assert all({key: row[key] for key in original[i]} == original[i] for i, row in enumerate(augmented))
    baseline = dict(schema_version=1, mode="live", layers={})
    assert compare_baseline(dict(baseline, evaluator_version="new"), baseline)["status"] == "incompatible"
    policy = json.loads((directory / "quality_policy.json").read_text(encoding="utf-8"))
    policy["cases"]["MH-001"]["question"] = "changed"
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot change"):
        apply_quality_policy(raw, path, directory / "single_turn.jsonl")


def test_cli_semantic_review_roundtrip_never_calls_models_or_relabels_history(tmp_path):
    from scripts.prepare_semantic_reviews import prepare
    c, context = mh001()
    response = semantic_response(context, c["reference_answer"] + "[资料1][资料2]")
    trace = dict(id=c["id"], question_hash=hashlib.sha256(c["question"].encode()).hexdigest(),
                 response=response, stages=dict(rerank=context, context=context))
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(json.dumps(trace, ensure_ascii=False) + "\n", encoding="utf-8")
    common = ["--mode", "replay", "--layers", "semantics", "--case-ids", "MH-001",
              "--trace-input", str(trace_path), "--strict"]
    source = tmp_path / "source"
    assert main([*common, "--output-dir", str(source)]) == 2
    reviews = tmp_path / "reviews.jsonl"
    info = prepare(ROOT / "evals/current", source / "report.json", trace_path, reviews)
    assert info == dict(review_items=1, unobserved_cases=0, model_calls=0)
    review = json.loads(reviews.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="reviewer"):
        apply_semantic_reviews(dict(records=[score_trace(c, trace, include_answer=True)]), reviews)
    review.update(verdict="passed", reviewer="reviewer-2", reason="按实际原文逐项核对条件和引用")
    reviews.write_text(json.dumps(review, ensure_ascii=False) + "\n", encoding="utf-8")
    out = tmp_path / "reviewed"
    assert main([*common, "--semantic-reviews", str(reviews), "--output-dir", str(out)]) == 2  # replay remains labelled incomplete
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["layers"]["semantics"]["summary"]["passed"] == 1
    assert report["group_scores"]["multihop"]["layers"]["semantics"]["summary"]["passed"] == 1
    with pytest.raises(ValueError, match="never overwritten"):
        prepare(ROOT / "evals/current", source / "report.json", trace_path, reviews)
    trace["response"]["answer"] = "changed"
    trace_path.write_text(json.dumps(trace), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint"):
        prepare(ROOT / "evals/current", source / "report.json", trace_path, tmp_path / "stale.jsonl")


def test_review_cannot_certify_an_unobserved_context(tmp_path):
    c = case()
    r = dict(answerable=True, answer="首次响应为8分钟", citations=[])
    record = score_trace(c, dict(response=r, stages={}), include_answer=True)
    binding = record["metrics"]["semantics"]["review_binding"]
    path = tmp_path / "review.jsonl"
    path.write_text(json.dumps(dict(id=c["id"], binding=binding, verdict="passed", reviewer="reviewer", reason="checked")), encoding="utf-8")
    with pytest.raises(ValueError, match="observed context"):
        apply_semantic_reviews(dict(records=[record]), path)
