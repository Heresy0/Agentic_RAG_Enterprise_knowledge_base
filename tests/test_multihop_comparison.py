from types import SimpleNamespace

from scripts.evaluate_multihop_comparison import (
    baseline_settings, comparison_order, comparison_settings, paired_order,
)


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


def test_parent_ablation_isolates_parent_flag_and_optional_controls():
    current=SimpleNamespace(agentic_rag_mode='auto', rag_planner_mode='auto',
        rag_repair_enabled=True, rag_feedback_enabled=True,
        rag_parent_context_enabled=True, rag_rerank_top_k=12, chat_model='same-model')
    before=vars(current).copy()
    variants=comparison_settings(current, 'parent-ablation')
    assert tuple(variants)==('ordinary_child','current_child','current_parent')
    assert vars(current)==before==vars(variants['current_parent'])
    assert {k for k in before if vars(variants['current_child'])[k]!=before[k]}=={'rag_parent_context_enabled'}
    assert vars(variants['ordinary_child'])==vars(baseline_settings(variants['current_child']))
    assert not variants['ordinary_child'].rag_parent_context_enabled
    assert comparison_settings(current, 'paired')['current'] is current


def test_three_arm_order_balances_every_position_and_pairwise_precedence():
    names=('ordinary_child','current_child','current_parent')
    orders=[comparison_order(i,names) for i in range(12)]
    assert len(set(orders))==6
    assert all(set(order)==set(names) for order in orders)
    assert all(sum(order[position]==name for order in orders)==4
               for name in names for position in range(3))
    assert all(sum(order.index(a)<order.index(b) for order in orders)==6
               for a in names for b in names if a!=b)
    assert all(comparison_order(i,('current','ordinary'))==paired_order(i) for i in range(12))
