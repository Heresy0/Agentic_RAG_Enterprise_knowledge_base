# 星海100题评估集 v4

主单轮共100题：原80题逐字保留，增加NQ-001至NQ-020；87道可回答、13道资料不足。
原XH题业务日期为2026-10-04、CC题为2026-10-13，新增NQ题为2026-10-16；明确日期仍以各题为准。
另继承原8组24轮多轮与12项权限用例，共120个案例、136个检查步骤；本次未新增多轮或权限状态案例。

新增12份南栖文档分布在现有四库，每库3份。统一解析语料包含原50份、原6份多跳增量和本12份，共68份；100题的正例证据覆盖其中62份，原6份多跳资料仍作为检索背景。数据库可能另有未列入此清单的旧文档。
该集合属于同源合成功能回归，不是从未参与开发的独立企业盲测。新增规则只约束南栖项目，不填补原80题资料不足项所缺事实。

每道单轮题使用新会话；多轮只保留组内历史。题目、参考答案、manifest和证据标注均不上传知识库。
旧80题保留于evals/xinghai_v3，旧报告不改写；100题与80题不得直接比较总体通过率。
自动评分按既有事实短语、引用及标注证据检查；semantic_checks仍需人工审核。关键数字必须绑定正确角色、日期和对象，关键词齐全不等于语义正确。

人工口径延续原题集：可回答题每题5分（事实2、必要限定1、引用支持2），资料不足题每题5分（不编造3、信息边界1、引用使用1）。单轮共500分，多轮120分；权限12项单独判定，任何泄漏为安全失败，未测试不得记为通过。

```powershell
python scripts/build_100_evaluation.py --prepare-corpus
python scripts/build_100_evaluation.py --check
python scripts/evaluate_system.py --mode offline --layers parsing --ocr --strict
# 真实评估须配置有效JWT，使用已有授权和模型；不会自动创建账号或扩大权限。
python scripts/evaluate_system.py --mode live --config evals/system_config.local.json --layers retrieval,rerank,context,answer --allow-model-calls --save-traces
```

默认统一入口使用本目录及output/knowledge-corpus-100。仅测新增题可指定--dataset-dir evals/xinghai_nanqi，并显式选择retrieval,rerank,context,answer；增量目录没有多轮和权限文件，不能使用默认all。
生成和离线校验没有执行模型质量评估。入库收据与后续真实成绩另行记录，不以coverage.json冒充召回或准确率。
