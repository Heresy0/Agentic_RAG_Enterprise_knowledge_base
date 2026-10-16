# 当前统一评估套件

统一入口为 `python scripts/evaluate.py`。本目录由冻结归档生成；问题、原参考、事实标注、权限范围和业务日期原样保留。此套件是合成回归集，不是独立业务盲测集。

| 分组 | 题数 | 可回答 / 资料不足 | 用途 |
|---|---:|---:|---|
| `main` | 100 | 87 / 13 | 原100题主回归 |
| `multihop` | 12 | 9 / 3 | 复杂多跳及普通RAG、父块消融 |
| `completeness` | 8 | 8 / 0 | 首次回答、多要点完整性 |
| 单轮合计 | 120 | 104 / 16 | 每题独立处理，三个分组分别评分 |
| 多轮 | 8组24轮 | 按原标注 | 独立会话内的追问与历史承接 |
| 权限 | 12项 | 按原身份范围 | 授权、越权与状态边界专项 |
| 性能负载 | 8个复用问题 | 6 / 2 | 3简单、3复杂、2拒答，不新增质量题计数 |

单轮、多轮组和权限项合计140个案例单元、156个检查步骤；这不是140道单轮题。`groups/`、`by_scope/`、`cross_scope.jsonl`、`unanswerable.jsonl`均为生成视图，不是新增题目。题型保留原`category`，不改成新的事实标准。

- [全部120题](测试题册.md)、[原参考与事实标注](答案与评分.md)。
- `suite.json`记录分组题号、来源及哈希；`scoring_rules.json`从[当前规则](../scoring_rules.json)生成。r10仅补齐CC-005的否定等价表达及反向保护，保留原r9其余规则和全部事实要求；归档与历史报告仍保留原规则。
- `conversations.json`、`permissions.json`逐字继承原100题配置。
- `groups/main`、`groups/multihop`、`groups/completeness`分别保留原始JSONL字节，便于历史回放和专项工具复用。
- 原`semantic_checks`、`forbidden_claims`及多跳`hop_steps`保留，不能宣称所有语义要求均由规则自动执行。
- 指定语料仍为`output/knowledge-corpus-100`的68份原文，题集和答案不得入库。

## 离线检查与统一运行

在项目根目录执行；以下检查不调用模型：

```powershell
python scripts/evaluate.py catalog
python scripts/evaluate.py check
python scripts/build_100_evaluation.py --prepare-corpus
python scripts/evaluate.py system --mode offline --layers parsing --ocr --strict
```

`check`验证归档哈希及所有生成文件；更新生成视图用`python scripts/build_evaluation_suite.py`。旧生成器仍可验证/重建其历史快照，目标已迁往归档；修改归档会导致套件完整性检查失败。OCR环境不足会明确显示未测试，不自动通过。

真实质量评估需要现有身份、权限及显式模型调用参数：

```powershell
# 全部120道单轮，报告同时保留三个分组成绩
python scripts/evaluate.py system --mode live --config evals/system_config.local.json --layers retrieval,rerank,context,answer --allow-model-calls --save-traces

# 单组；--group可重复，先选组/题号再应用--limit
python scripts/evaluate.py system --mode live --config evals/system_config.local.json --group main --layers retrieval,rerank,context,answer --allow-model-calls --save-traces
python scripts/evaluate.py system --mode live --config evals/system_config.local.json --group multihop --group completeness --layers retrieval,rerank,context,answer --allow-model-calls --save-traces

# 多轮、权限及性能仍按各自分母评分；--group只筛选单轮题
python scripts/evaluate.py system --mode live --config evals/system_config.local.json --layers conversations --allow-model-calls --allow-conversation-writes
python scripts/evaluate.py system --mode live --config evals/system_config.local.json --layers security --allow-model-calls
python scripts/evaluate.py load --profile mixed --max-requests 24 --users 2 --preview --output-dir data/load-tests/new-batch --dry-run
```

性能入口不因整合自动发压测请求，正式运行仍须有效身份与`--allow-model-calls`。权限状态型用例未准备时保留skipped。`system --performance-report`仍可导入既有压测报告，不等于新压测。

## 回放、Ragas与对照

```powershell
python scripts/evaluate.py system --mode replay --group main --layers retrieval,rerank,context,answer --trace-input evals/reports/system/某批次/traces.jsonl
python scripts/evaluate.py multihop-compare --help
python scripts/evaluate.py ragas --help
python scripts/evaluate.py ragas-compare --help
```

只存在旧100题trace时，使用`--group main`。若选择全120题，其余20题明确标记缺失观察；不会把历史94/100变成120题新成绩。`report.json`的`group_scores`及`report.md`的分组表分别记录每组通过/失败/错误/缺失观察、Hit Rate、MRR和证据完整率。首次生成指标也按组保留于JSON。

Ragas默认使用`groups/main/single_turn.jsonl`，补全参考文件为`groups/main/ragas_references_v1.json`，只适用于main组。当前参考文本未改，仅同步r10评分指纹；复现旧r9报告时应显式使用`evals/archive/xinghai_v4/single_turn.jsonl`及该归档目录的参考文件。新120题Ragas可显式传`--dataset evals/current/single_turn.jsonl`及同一120题来源报告、trace；不能把100题参考覆盖套到120题，也不自动生成新增金标。Ragas仍单独记录裁判费用，不改变原规则分。

多跳对照默认使用`groups/multihop`，其12题字节、日期与评分不变。旧子脚本保留兼容性，正式文档统一使用`evaluate.py`入口。模型调用、会话写入和输出覆盖保护继续由原工具执行。

## 历史与比较边界

归档位置见[归档索引](../archive/README.md)。历史报告、验收收据仍在原路径，未重写。历史94/100、12题对照和Ragas分数各有原分母，不作为新120题实测。比较报告时继续校验模式、题集/规则/语料/授权指纹，并加入套件和分组选择；不同口径不硬算提升。

本次迁移验证见[整合收据](../acceptance/suite_consolidation_20261016.json)：1104项回归通过、48项跳过，旧100题回放的逐题答案/状态/指标与94/100原报告一致，427份历史文件哈希未变。本机OCR环境缺失，离线解析170项通过、3项跳过（1份扫描件及2道关联证据检查），不作为完整OCR验收。
