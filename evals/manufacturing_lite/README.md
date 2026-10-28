# 制造业回归集 v1

20 道人工编写的合成回归题：16 道可回答、4 道资料不足题。覆盖工艺版本、在制工单例外、表格参数、跨页条件、量具隔离与借用、跨文档查询，以及缺少转速、完成时间、实时库存和未知批次时的拒答。

本题集与原 120 题评估套件独立，不能把原套件成绩写成制造业成绩。[真实 HTTP 首轮验收](reports/20261028-live-r1/README.md)已完成全部入库与 20 题运行：自动评分 14/20、助手非独立复核 18/20；两题真实失败保留，原题目及标注未改动。

- `single_turn.jsonl` 沿用现有评估 schema，包含问题、参考答案、关键事实和原文证据；不得入库。
- `as_of` 是标注背景，现有运行器不会将它自动拼入问题。业务日期须在问题中明确提供，实际开工记录也可以作为证据。
- `role_permissions.json` 是离线角色夹具；真实验收另外创建了隔离演示账号并验证实际 HTTP 权限，不能仅由此文件判断任何其他环境已经配置。
- `subjects.example.json` 供新演示租户初始化使用，先换成五个不同、已经创建的 Keycloak 身份 sub。
- `system_config.example.json` 的 UUID 都是占位值。真实评测前换成新演示租户返回的三个知识库 ID，并设置知识管理员的 `EVAL_MANUFACTURING_ACCESS_TOKEN` 环境变量；有效授权范围必须恰好等于配置的三个库。
- 不复用原 IT 语料的 HTTP 权限夹具；真实制造业权限检查单独记录在首轮报告中。角色单元回归通过不等于真实账号的 HTTP 越权验证通过。

含 OCR 的离线解析可使用现有统一入口：

```powershell
.venv/Scripts/python.exe scripts/evaluate.py system --mode offline --dataset-dir evals/manufacturing_lite --corpus sample_docs/manufacturing_lite --layers parsing --ocr --strict --output-dir tmp/mfg-parsing-new
```

运行环境须有 Tesseract 中英文语言包和 Poppler。没有运行 OCR 时会标为未完成；不能用人工标注替代识别结果。专用验证器还检查参数整行对应、跨页条件、表头与代码围栏保真，详见 [实施说明](../../docs/MANUFACTURING_LITE.md)。

真正入库、完成认证配置后，才使用下面的显式模型调用命令。它调用 Embedding、重排和生成，可能消耗模型额度；`--save-traces` 会保存获授权的正文，应将输出留在本地内部目录。

```powershell
.venv/Scripts/python.exe scripts/evaluate.py system --mode live --dataset-dir evals/manufacturing_lite --corpus sample_docs/manufacturing_lite --config tmp/mfg-system-config.json --layers index,retrieval,rerank,context,answer --allow-model-calls --save-traces --output-dir tmp/mfg-live-new
```

自动关键词分数只是代理指标，尤其需复核完整条件、数值与对象绑定、版本例外和拒答理由。标准或事实的同义表达如需补充，须在版本化标注中记录，不为提高单次成绩临时放宽规则。不得把已知标准答案输入生成模型充当检索结果。

原离线报告位于 `reports/20261028/`，保留首次失败及校对后结果；真实 HTTP 问答和复核另存于 `reports/20261028-live-r1/`，两者不混用成绩。
