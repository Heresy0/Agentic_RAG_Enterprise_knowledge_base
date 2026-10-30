# 首位排序与语义正确性评估

2026-10-21 新增 `ranking`、`semantics` 两层。原 120 题问题、参考、必答事实、r10 自动代理分及冻结归档不变；新检查以 `2026-10-21-quality-v1` 和独立策略哈希标识。历史通过率不会自动变成新体系成绩。

## 排序检查

各阶段增加 Hit@1、Hit@3、Hit@5，保留 MRR、全部必要证据覆盖。Hit@1 表示首条包含一个标注支持证据，不证明它是唯一最优答案；多跳中的桥接证据也可能计为相关。

`ranking` 默认要求重排结果和实际上下文的首个标注证据位于第 1 条。只要任一必要阶段首位不命中，即使证据覆盖为 100%，这一层仍失败。实际最终回答和上下文均存在时，另外检查最终引用列表的首位。资料不足题没有正例排名标注时记为不适用；缺观察记为未测试。该层不评价未被展示的候选列表是否好看，也不代替人工检查核心结论顺序。

有业务理由时，可在版本化策略中对具体题声明 `ranking_checks.max_first_relevant_rank`，不能在看到运行答案后随意放宽。没有分级标注时 NDCG 为 null。可为题目增加以下 `relevance_judgments`：

```json
{
  "complete": true,
  "items": [
    {"document": "响应约定.md", "text": "首次响应为8分钟", "grade": 3},
    {"document": "日志手册.md", "text": "日志保留30天", "grade": 0}
  ]
}
```

分值范围为 0—3。NDCG@5 的理想列表使用全部已标注项，包括未召回的正例。首屏存在未标注项则不计算 NDCG、不将它当成零相关；此时排序层不完整。存在完整分级标注时，还要求首位达到最高相关等级。重复同一个已标注片段不会重复获得收益。标注应覆盖待比较方案的候选并集，随后冻结。

过程记录新增 `rerank_model`，保存业务规则干预前的模型顺序；原 `rerank` 仍表示规则处理后的顺序。保留 RRF、模型排名、最终排名和重排分数。比较向量/BM25→融合、模型→业务规则、父块→Agent、最终候选→上下文、上下文→引用的证据丢失及首个正确证据后移。上下文选择记录重复、每来源限额和字符预算排除原因。旧轨迹没有这些观察时，不推测模型原本排在何处。

## 语义检查与独立复核

`semantics` 分开报告：

1. 有审核标注的有限语义断言：要求出现的完整条件、禁止的相反结论、同一结论上的来源绑定，以及已有逐事实否定/矛盾规则。
2. 独立复核：核对对象、数值与单位、全部条件、否定和比较方向、逐结论引用、核心结论位置。

新增策略源为 `evals/quality_policy.json`，生成器同步到 `evals/current/quality_policy.json`，并纳入套件哈希。本版对已复现的 MH-001 加入规则绑定、连续两轮、零缺失、附件一致、缺陷关闭及反向放行断言；其他题继续复用已有适用的 `fact_assertions`。这不是为所有 120 题新增完整语义金标，覆盖数由报告的 `assertion_covered_cases` 明确列出。

有限断言发现错误即失败；断言通过但没有独立复核时记为 partial，没有断言且没有复核时记为 skipped。完整通过必须有绑定本次问题、最终响应、实际上下文及有效评分标准的复核结果。复核 uncertain 不算通过，复核 passed 也不能覆盖明确失败的断言。自动代理分与这一层分开，关键词齐全仍可能是代理通过而语义失败。

## 使用

所有命令均从项目根目录执行。回放不调用模型：

```powershell
python scripts/evaluate.py system --mode replay `
  --layers retrieval,rerank,context,answer,ranking,semantics `
  --trace-input evals/reports/system/原批次/traces.jsonl `
  --output-dir evals/reports/system/quality-review-new --strict
```

默认 `--layers all` 已包含两个新层。仅选择原层仍可查看历史代理指标。真实运行沿用 `--mode live --config ... --allow-model-calls --save-traces` 的显式开关；新增层不会自行启用付费调用。

先导出待复核文件：

```powershell
python scripts/evaluate.py review-pack `
  --source-report evals/reports/system/quality-review-new/report.json `
  --trace-input evals/reports/system/原批次/traces.jsonl `
  --output evals/reports/system/quality-review-new/reviews.jsonl
```

每行包含问题、标准、最终回答和实际上下文。独立审阅后填写 `reviewer`、`reason` 和 `verdict`（passed / failed / uncertain），保留 binding 及原始输入。文件包含授权原文，按原轨迹相同的访问范围保管。导出不调用裁判模型；复核来源及其独立性仍需实验组织者确认，导入器不会把一段自称独立的模型输出当成独立性证明。

在另一个新目录回放并导入：

```powershell
python scripts/evaluate.py system --mode replay --layers ranking,semantics `
  --trace-input evals/reports/system/原批次/traces.jsonl `
  --semantic-reviews evals/reports/system/quality-review-new/reviews.jsonl `
  --output-dir evals/reports/system/quality-reviewed-new --strict
```

缺失、重复、过期或无实际上下文的复核不能通过。复核文件自带的原始输入若与绑定不符也会拒绝。报告保留审核绑定与理由；Ragas 连续分仍单独保存，不能未经校准直接转换为 passed 复核。

退出码：检查失败为 1；`--strict` 下观察不完整为 2；其余为 0。历史 replay 始终保留“不等同新实测”的不完整标记，即使全部已观察项目通过，也不会被标成新的 live 验收。

## 回归与验收边界

```powershell
python scripts/evaluate.py check
python -m pytest tests/test_quality_evaluation.py -p no:locust -q
```

新增回归覆盖已审计的融合丢失关键词首位、主题规则覆盖模型排名、版本号问法变化、父块合并缺证据、Agent 合并后移、长证据预算跳过、引用顺序、反向语义与条件变异、错误来源绑定、复核篡改及 CLI 退出状态。冻结最小观察用于验证评估器能识别问题，不要求业务检索永远维持旧错误。

这些改动增加检查和诊断，不修改 RRF 权重、业务规则优先级或 Agent 合并策略。对现有轨迹运行新检查可能暴露原有失败；新增测试通过表示评估器能发现所覆盖的问题，不表示线上排序缺陷已经修复。业务效果仍需冻结独立问题集并进行真实运行和复核。

2026-10-22 本地验证：非集成测试 1,140 项通过，48 项集成测试未运行；120 题套件校验通过。对 2026-10-16 原始 120 题轨迹进行离线回放，新排序门槛为 87 题通过、17 题失败、16 题不适用；104 道正向证据题的重排 Hit@1 为 83.65%，上下文 Hit@1 为 94.23%。语义断言覆盖 11 题，2 题失败、9 题待独立复核，其余 109 题没有断言；本次没有生成完整语义通过率，也没有新模型调用。可审计汇总见 [quality_checks_20261022.json](../evals/acceptance/quality_checks_20261022.json)。
