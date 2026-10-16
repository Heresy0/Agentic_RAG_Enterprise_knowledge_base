# 复杂与多跳增量题集

12道新题：4道两跳、2道三跳、3道复杂组合、3道拒答边界；9道可回答、3道不可回答。
配套六份源文件在`sample_docs/xinghai_multihop/documents/`，按技术部2份、平台研发部4份划分。
原`evals/xinghai_v3`的80题逐字保留；本目录是增量集，仍使用现有评估入口，避免破坏旧报告的题集哈希。

- `single_turn.jsonl`：标准问题、参考答案、必答事实、原文证据、权限范围，兼容现有评估器。
- `hop_steps`：依赖序号、来源、原文摘录、中间事实与必要补充证据。`intermediate_entities`未提前写入问题。
- `diagnostic_evidence`：三道负例的缺资料/冲突/计划边界；不冒充正例召回标签。
- `测试题册.md`与`答案与证据链.md`：便于内容审核。不要入库，也不会传入问答模型。
- `coverage.json`：语料哈希、题目分布与验证边界。

“跳”指逻辑证据依赖，不要求Agent固定调用同等次数的工具。一次检索拿齐资料也可能完成多跳推理。
现有自动评分检查事实短语、来源和证据召回，**不会自动执行hop_steps的语义判定**。
需另行人工核对中间绑定、两个时钟、相似事件对象与最终结论，并从trace观察实际工具行为。
该数据由同一开发过程生成与标注，不是严格独立企业盲测；不能用12题证明Agent优于普通RAG。

静态生成和证据校验无需模型或数据库：

```powershell
.venv\Scripts\python.exe -X utf8 scripts/build_multihop_expansion.py --check
```

正文确认并完成入库后，使用具有相应读取权限的真实账号评估。跨部门题要求技术部和平台研发部都可读，
不得为通过测试扩大当前用户权限。`evals/system_config.local.json`映射现有知识库，JWT从环境变量读取。

```powershell
.venv\Scripts\python.exe -X utf8 scripts/build_multihop_expansion.py --prepare-corpus
.venv\Scripts\python.exe -X utf8 scripts/evaluate_system.py --mode live --config evals/system_config.local.json --dataset-dir evals/xinghai_multihop --corpus output/knowledge-corpus-multihop --layers retrieval,rerank,context,answer --allow-model-calls --save-traces
```

该目录没有新增多轮或权限状态题，不使用默认`--layers all`。真实评估会调用收费模型，本轮只准备材料并执行离线校验，尚未执行上述真实评估命令。
增加语料后重新跑旧80题时，也应记录当前56份语料清单，不能与旧50份语料报告直接宣称同条件提升。
现有调用/搜索预算保持不变，题目失败不自动触发扩预算、父子块或检索路由改造。
