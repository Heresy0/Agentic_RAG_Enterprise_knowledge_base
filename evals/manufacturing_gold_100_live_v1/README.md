# 百题隔离运行入口 v1

本入口执行 `../manufacturing_gold_100_v1/` 的冻结候选，不修改原题、标准答案、原始40题或历史成绩。候选经过助手非独立复核，独立人工/工厂批准为0；不是独立盲测。

## 运行范围

- 100题：28道制造问答、62道通用知识问答、10道会话协议。54道新资料题走正常知识问答，不伪装成ERP/MES实时字段题；6道新协议题必须先把离线别名对应到新副本真实ID。
- 新建两个平台数据库副本、独立Redis、原件卷和Milvus集合；ERP复用原只读上游，MES使用独立OpenMES及其数据库。原环境不更新。正常适配器始终只读；故障控制器仅能针对新MES副本，通过真实管理API制造与恢复来源变化。
- 旧13份原文、53块及审核记录原样复制；新增42份原文索引一次，832块在两个平台副本复用，共55份/885块。新库为显式单库范围；旧业务选择保留，未指定知识库时按应用正常行为检索当前全部可读库（包括新扩展库）。
- 6份现行指导书经原文锚点核对后，调用现有审核发布与步骤映射API。审核说明明确是助手非独立、隔离演示用途，不是工厂批准，也不修改离线黄金候选的审核状态。

## 一次执行与预算

当前用户授权为“准备隔离夹具，准备好后可以调用真实模型完整跑一遍”。以下数值是操作者设置的安全上限，并非旧16题授权的延续：

| 阶段 | 物理调用 | 预算Token | 秒数 |
| --- | ---: | ---: | ---: |
| 42文件一次性索引 | 128 | 1,000,000 | 1,800 |
| 完整100题 | 800 | 5,000,000 | 9,000 |

未知用量每次预留32,000 Token；每个物理调用前检查预算。问答的受限内部修复也计入调用；单题不重试、整轮不补跑、不拼分。索引独立计账，不算问答调用。使用现有供应商配置，未配置价格则费用未知。只有普通问题、授权检索原文和演示白名单字段进入模型，金标和评分结论不进入请求。

准备阶段可以修复零调用的环境错误；实际索引和百题执行分别以独占标记锁定，不能覆盖已有执行结果。故障题、确定性字段及拒答可能零调用，不强求100题都请求生成模型。

## 文件与检查

- `scripts/manufacturing_gold_fixture_plan.py`：只读运输计划。
- `services/openmes_adapter/provision_gold_fixture.php`：仅新MES副本的管理种子。
- `scripts/prepare_manufacturing_gold_runtime.py`：新库上传、单次索引、原文审核及不可变分块复制。
- `scripts/manufacturing_gold_live.py`：权限/来源不替换的API执行、控制夹具、硬预算、会话协议和结构检查。
- `scripts/run_manufacturing_gold100_isolated.py`：主机协调器，冻结绑定后逐题单次执行，使用跨进程共享的总预算。

本机私有配置、令牌、原始响应、Trace、快照及独立账本位于被忽略的 `tmp/manufacturing-gold-100-live-20261112-r1/`。不得提交凭据。新克隆不能把这些路径视为默认部署配置。

```powershell
.venv/Scripts/python.exe -m pytest tests/test_manufacturing_gold_live.py -q
.venv/Scripts/python.exe -m scripts.check_manufacturing_gold_100
.venv/Scripts/python.exe -m scripts.run_manufacturing_gold100_isolated --run-dir tmp/manufacturing-gold-100-live-20261112-r1 --prepare-only
# 仅在本轮用户授权、隔离资源、零模型探针与冻结输入均有效时执行一次：
.venv/Scripts/python.exe -m scripts.run_manufacturing_gold100_isolated --run-dir tmp/manufacturing-gold-100-live-20261112-r1
```

HTTP、可回答标志、来源数量等仅为结构/边界检查；最终语义按冻结必答点、禁止结论和引用逐题复核，单独标为助手非独立，不把结构通过当准确率。100题有同类产品参数化重复，不能称作100种独立技能。
