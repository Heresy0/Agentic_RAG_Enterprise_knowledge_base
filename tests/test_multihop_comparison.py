from types import SimpleNamespace

from scripts.evaluate_multihop_comparison import baseline_settings, paired_order


def test_baseline_changes_only_optional_multistep_controls():
    current=SimpleNamespace(agentic_rag_mode='auto', rag_planner_mode='auto',
        rag_repair_enabled=True, rag_feedback_enabled=True,
        rag_parent_context_enabled=True, rag_rerank_top_k=12,
        chat_model='same-model', embedding_model='same-embedding', rerank_model='same-rerank',
        chat_api_key='same-private-credential', context_limit=12000)
    before=vars(current).copy()
    baseline=baseline_settings(current)
    assert vars(current)==before
    changes={key for key,value in before.items() if getattr(baseline,key)!=value}
    assert changes=={'agentic_rag_mode','rag_planner_mode','rag_repair_enabled','rag_feedback_enabled'}
    assert baseline.rag_parent_context_enabled and baseline.rag_rerank_top_k==12


def test_each_case_runs_both_arms_and_first_position_is_balanced():
    order=[paired_order(index) for index in range(12)]
    assert all(set(pair)=={'current','ordinary'} for pair in order)
    assert sum(pair[0]=='current' for pair in order)==6
    assert sum(pair[0]=='ordinary' for pair in order)==6
