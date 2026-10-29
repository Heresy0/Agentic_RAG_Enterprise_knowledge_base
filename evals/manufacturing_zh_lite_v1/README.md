# 制造业精简演示与回归集 v1

本版本服务于面试演示和日常回归。原18份资料、120题和旧成绩保留在`manufacturing_zh_v3`与`manufacturing_closed_loop`，本目录是独立选择配置，未覆盖原题或旧报告。

| 分组 | 普通知识题 | OCR题 | 拒答／澄清题 | 合计 |
| --- | ---: | ---: | ---: | ---: |
| 开发 | 30 | 0 | 10 | 40 |
| 精简回归 | 30 | 8 | 20 | 58 |
| 合计 | 60 | 8 | 30 | 98 |

全部90道非OCR题保留，包括未通过题与超时题。OCR保留原先通过的Q056、Q058、Q067、Q074、Q076，以及代表性失败Q068、Q071、Q077。5道通过题分布在4份扫描件中，3道失败题同样来自这些资料，所以实际演示库为12份普通资料＋4份扫描件，共16份；MFG-ZH11和MFG-ZH13仅退出本演示库，原件继续留档。

选择已经使用已知结果，不称独立留出或盲测。保留题目的问题、参考答案、原始事实标签与来源范围全部来自原题，按对象逐项校验相等。后续重评分通过独立的`scoring_rules.json`叠加，不修改题集原始字段。

## 初版评分修订（r12）

- 14题补充经原文核对的等价表达，覆盖数量词、语序、量程括号、单位邻接和完整否定判断。不是全局把“并非”替换成“不是”。
- Q044只问安装方式与重复次数，将“取平均值”改为选答；平行和3次仍必答。
- Q084只问是否包含零点，将“至少5点、满量程”改为选答；排除零点仍必答。
- Q018的闭环、Q019的全过程，以及Q008、Q034等真实缺项继续保留要求。N25等澄清题也没有为当前模型放宽预期行为。
- 新标注绑定问题SHA-256。短标签“不是、不够”等可禁用独立字面匹配，必须命中限定对象的完整判断；保留反向结论、矛盾、数值边界、引用与来源检查。
- 程序修复数量别名可能把5识别成50、9个点识别成19个点的边界缺陷。
- 回答端只增加通用约束：区分通用说明与针对当前对象的选择／判断；缺必要用户条件先追问；操作就绪问题覆盖必需准备步骤和顺序。没有按题号、设备或标准数值添加回答特例，没有新增分类模型或改检索架构。

对同一批历史回答的98题子集重放，原评分66题通过，修订评分82题通过，16题变化全部来自评分修订，不能说成模型能力提升。历史回答来自原18份资料环境，新16份资料环境的实际结果另行记录。所有审核均标为助手非独立复核，自动代理分数不等于语义准确率。

## 新环境实测结果

新16份资料环境已完成一轮98题真实HTTP验收：普通题49/60，OCR题5/8，拒答与澄清23/30，合计77/98（自动代理评分）。其中1题生成超时，计入未通过且未重试。5道历史OCR通过题本轮全部通过，3道代表性失败题仍保留。新回答仍有评分措辞边界和真实漏项，未据此继续改题或调分。完整结果、剩余问题、权限及基线保留证据见[20261029-r1报告](reports/20261029-r1/README.md)。

随后按用户授权，仅修正5道等价表达误判和N16/N21的标注边界。r13对上述同一批回答离线重评分为84/98：普通题54/60，OCR题5/8，拒答与澄清25/30。模型调用数为0，7项变化全部来自评分与标注纠正。原r12规则另存为`scoring_rules_r12.json`，原报告保持原样，详见[r13离线复核](reports/20261029-r2-scoring/README.md)。

最新已完成[链路收尾验收](reports/20261029-r3-workflow/README.md)：三服务统一r13，审核样本携带评分修订进入Worker；Q043真实问题完成反馈、样本版本、修复前后2题对照及验证关闭。该局部对照不更新98题总分。启动和约7分钟的展示路径见[制造业演示指南](../../docs/MANUFACTURING_DEMO.md)。

## 入口与复验

`profile.json`绑定原始语料清单、所选原件哈希、两个分组和原题指纹；两份JSONL均可独立查看。`build_manufacturing_demo_profile.py`可确定性生成并复核选择，已存在的不同内容不会被覆盖。

新环境使用独立租户；原18份演示库保持原样。账号和令牌仅保存在运行目录的私有JSON中，按原OIDC流程登录新演示账号。

```powershell
$runDir = 'tmp/manufacturing-demo-lite-新轮次'
$profile = 'evals/manufacturing_zh_lite_v1/profile.json'
.venv/Scripts/python.exe scripts/manufacturing_workflow.py before --run-dir $runDir --baseline-ref caf26ea
.venv/Scripts/python.exe scripts/manufacturing_workflow.py provision --run-dir $runDir --apply
.venv/Scripts/python.exe scripts/manufacturing_workflow.py tokens --run-dir $runDir --apply
.venv/Scripts/python.exe scripts/manufacturing_workflow.py upload --run-dir $runDir --profile $profile --apply --allow-model-calls
.venv/Scripts/python.exe scripts/manufacturing_workflow.py status --run-dir $runDir
# 完成解析后核对原件与分块，再明确填写复核范围及OCR限制。
.venv/Scripts/python.exe scripts/manufacturing_workflow.py publish --run-dir $runDir --apply --acknowledge-ocr --review-note '填写实际检查范围与剩余问题'
.venv/Scripts/python.exe scripts/manufacturing_workflow.py audit --run-dir $runDir
.venv/Scripts/python.exe scripts/run_manufacturing_http_acceptance.py --config "$runDir/system_config.json" --tokens "$runDir/tokens.json" --profile $profile --split development --max-questions 40 --output-dir "$runDir/development-http-r1" --refresh-local-keycloak-token --allow-model-calls
.venv/Scripts/python.exe scripts/run_manufacturing_http_acceptance.py --config "$runDir/system_config.json" --tokens "$runDir/tokens.json" --profile $profile --split regression --max-questions 58 --output-dir "$runDir/regression-http-r1" --refresh-local-keycloak-token --allow-model-calls
.venv/Scripts/python.exe scripts/report_manufacturing_demo.py --run-dir $runDir --output-dir "$runDir/report" --collect-ledger
.venv/Scripts/python.exe scripts/manufacturing_workflow.py after --run-dir $runDir
.venv/Scripts/python.exe scripts/manufacturing_workflow.py preserve --run-dir $runDir
```

摄入和问答均需明确允许模型调用；HTTP批次预算为题数上限，保留既有服务超时与单请求RAG约束，不声称金额上限。每轮新建目录，失败保留且不自动重复问答择优记分。历史重评分可用`replay_manufacturing_demo.py`单独执行，模型调用数为0。
