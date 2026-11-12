# 百题v2隔离单次模型验证

本入口使用`../manufacturing_gold_100_v2/`的冻结问题、答案与证据，业务基线`b741bd2`。保留v1题集、运行器、原始响应和结构89/100、助手非独立语义69/100；不回算或拼分。v2为助手非独立复核候选，独立人工/工厂批准0，不是盲测。

用户本轮授权“调用模型跑一次”。操作者设置安全上限：仅3份格式修订资料索引32次物理调用/100万预算Token/1800秒；完整100题及其协议前置步骤800次/500万预算Token/9000秒。未知用量每次预留32000 Token；受限内部修复计入预算，无单题重试、整轮补跑或续跑。使用现有百炼供应商，价格未配置则费用未知。金标、标准答案和评分意见不进入模型请求。

新建两个平台数据库/原件卷、Redis、向量集合及OpenMES克隆；ERP复用只读上游。复用未变52份原文的既有向量，仅索引3份格式修订，然后在新平台副本之间复制。原环境、原向量及账号权限保留；正常ERP/MES适配器仍只读，故障控制仅修改新MES克隆。

运输层复用v1的鉴权、API请求、预算及协议辅助函数；v2问题冻结、执行入口、故障控制目标和结果目录单独维护。新增3份台账通过现有上传、一次索引及审核发布路径处理，审核是隔离演示用途，不代表工厂或黄金样本批准。

```powershell
.venv/Scripts/python.exe -m pytest tests/test_manufacturing_gold_v2_live.py -q
.venv/Scripts/python.exe -m scripts.check_manufacturing_gold_100_v2 --probes
# 只有新隔离资源、零模型探针与冻结绑定就绪后执行一次；不能复用v1目录：
.venv/Scripts/python.exe -m scripts.run_manufacturing_gold100_v2_isolated --run-dir tmp/manufacturing-gold-100-v2-live-20261112-r1 --prepare-only
.venv/Scripts/python.exe -m scripts.run_manufacturing_gold100_v2_isolated --run-dir tmp/manufacturing-gold-100-v2-live-20261112-r1
```

结构检查与助手非独立语义复核分开报告；不能把HTTP成功或有引用当作语义正确。私有配置、令牌、Trace、账本和备份在`tmp/manufacturing-gold-100-v2-live-20261112-r1/`，不提交凭据；本机状态不视为新克隆的默认配置。
