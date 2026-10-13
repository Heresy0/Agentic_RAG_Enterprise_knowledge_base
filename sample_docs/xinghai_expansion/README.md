# 星海知识库小规模扩容包

新增20份Markdown文档：6份流程、8份项目/故障记录、4份历史版本、2份跨部门资料。
基准日期为2026-10-13；文档、企业业务事实与题集均为人工设计的合成回归资料，不是实际企业数据或盲测基准。
延续星海公司，新增澄川项目CC-2026-07；规则仅适用于该项目，原有30份文档和原题集保持原样。

| 知识库 | 文档数 | 目录 |
| --- | ---: | --- |
| 公司公共 | 5 | documents/01_公司公共 |
| 人力资源部 | 4 | documents/02_人力资源部 |
| 技术部 | 6 | documents/03_技术部 |
| 平台研发部 | 5 | documents/04_平台研发部 |

只上传documents中对应部门的业务文档；不要上传本README、manifest、入库清单、生成器数据或evals中的题目和参考答案。
公共入口不复写HR评审节点和平台故障细节；跨部门资料不会自动改变权限。使用现有知识库和真实获准账号，逐库入库。

## 生成与检查

生成只使用本地Python，不请求模型，不获取密钥，不连接数据库。固定事实源位于scripts/corpus_expansion_data.py。
再次生成时内容一致则无变化；若目标已有不同内容，默认停止，审核后才使用--overwrite替换生成文件。
--check只核对生成物，不写文件。它检查证据在原文中存在，实际解析检查须执行下面的parsing评估。

```powershell
.venv\Scripts\python.exe scripts/build_knowledge_expansion.py
.venv\Scripts\python.exe scripts/build_knowledge_expansion.py --check
.venv\Scripts\python.exe scripts/merge_xinghai_evaluation.py --prepare-corpus
.venv\Scripts\python.exe scripts/evaluate_system.py --mode offline --layers parsing
```

## 入库与评估

入库会调用Embedding，下面为单库示例；替换UUID，并为四个目录分别使用正确知识库ID和已获准用户ID。不要对包根目录递归入库。
批量入库CLI是管理工具，直接连接数据库，操作人须事先核实这些ID的租户归属和授权；浏览器上传仍由API校验权限。
此前已有按文件哈希、切分版本、OCR修订和模型判断的幂等索引；仅文件字节不变不保证跨版本始终跳过。

```powershell
.venv\Scripts\python.exe scripts/index_documents.py --directory sample_docs/xinghai_expansion/documents/01_公司公共 --tenant-id <租户UUID> --knowledge-base-id <公共库UUID> --user-id <获准用户UUID>
```

新增20题已合并至evals/xinghai_v3，主单轮题集共80题（71道可回答、9道资料不足）；CC-001至CC-020覆盖本包全部20份资料。
by_scope适合逐库执行；跨库题需要全部可访问模式且具备所有来源库权限，不能对单个固定库运行混合题集。
使用evals/system_config.example.json的格式准备本地配置，四个知识库名称分组不变；令牌只放环境变量。
线上评估先取少量案例；查询Embedding、重排和答案生成会计费。本次包生成及离线验证不代表已入库或实际问答通过。

```powershell
# 只测检索/重排/上下文，不执行答案生成；仍会调用Embedding和重排。
.venv\Scripts\python.exe scripts/evaluate_system.py --mode live --config evals/system_config.local.json --layers retrieval,rerank,context --case-ids CC-001 CC-002 CC-005 CC-011 CC-016 --allow-model-calls
# 确认检索后再显式执行完整80题，费用高于只测新增20题。
.venv\Scripts\python.exe scripts/evaluate_system.py --mode live --config evals/system_config.local.json --layers retrieval,rerank,context,answer --allow-model-calls
```

固定了项目事实再生成文档与题集，属于同源合成回归。测试计划/实际、历史/现行、相似根因及跨库证据，不能以文档数量推导生产容量。
本包没有新增多轮或自动权限场景，主目录保留原8组多轮及12项权限用例。单库内部信息不能因在公共入口被提及而泄漏。
coverage.json只记录文件哈希、规模及证据覆盖，不包含虚构的模型准确率或Token账单。
生成器使用相同模板的记录交接部分，因此性能规模与业务语义难度应分别报告，不能视为20份独立真实企业样本。
