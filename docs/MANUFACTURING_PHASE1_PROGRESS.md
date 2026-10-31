# 制造业首期工单集成实施进度

更新时间：2026 年 10 月 31 日。实施分支：`codex/manufacturing-phase1`。完整范围及门槛见[首期改造方案与验收标准](MANUFACTURING_PHASE1_PLAN.md)。

## 分支与基线

开始实施前已更新远程引用并核对祖先关系：现有本地和远程分支均已包含于 `origin/main`，没有额外待合并分支。已将采纳的方案及项目入口说明以 `9846c4e` 提交并推送主线，随后从该提交建立实施分支。

工作区原有的未跟踪 `output/` 内容未纳入本期提交。现有运行容器、业务数据库和已发布演示语料保持原状。

## 阶段状态

| 阶段 | 状态 | 已完成内容或下个出口 |
| --- | --- | --- |
| M0 契约与夹具 | 离线验收通过 | 来源及客户端请求契约、13 份资料清单、8 张工单、5 条待审核规则、40 题及只读检查工具 |
| M1 连接与授权 | 实现及隔离验证通过 | 独立只读模拟 MES、连接器、BusinessScope、授权工具、三张业务表及迁移、真实回环 HTTP 和故障检查 |
| M2 适用性 | 待实施 | 基于可信条件选版，规则证据获取与所有检索路径约束 |
| M3 问答与页面 | 待实施 | 制造 HTTP 入口、工单与文档两类证据、会话绑定与页面 |
| M4 闭环与验收 | 待实施 | 业务样本审核、冻结业务输入的 Worker、正式实测、权限及故障矩阵、部署与演示 |

当前可以独立运行 M1 只读模拟 MES，并通过服务层按数据库授权读取工单。普通问答仍不自动调用 MES，制造用户端问答和页面将在 M3 接入。后续阶段继续沿用本会话的实施授权；每阶段完成后记录实际检查和剩余项。

## M0 交付

`backend/app/manufacturing/contracts.py` 定义工单记录、服务端观测上下文、客户端制造问题、夹具容器和规则标注。校验保留日期精度、拒绝无时区时间及数字时间戳、区分未知值、限制字段，禁止客户端覆盖租户、工厂、源地址或实际开工事实。

[新语料清单](../sample_docs/manufacturing_workorder_v1/README.md)按原字节引用 10 份既有合成原件，新增 3 份具体工单通知。规则摘录逐项与原件核对，标记为 `draft`；没有导入运行库或自动批准发布。

[独立 40 题](../evals/manufacturing_workorder_v1/README.md)分为开发 16 题、回归 24 题，包含 30 道回答、4 道澄清和 6 道拒答。12 项关键问题已绑定题号，相关工单变体不跨分区。工单来源文件不包含标准答案或预期选版。

生成器对整批目标文件预检，只创建缺失文件；发现内容不同则拒绝。校验器核对原件哈希、证据摘录、业务字段、分组、schema、验收门槛、包定义及历史冻结文件；单独重算清单哈希不能使篡改的题集通过。

## M0 验证记录

| 检查 | 结果 |
| --- | --- |
| 新增契约及资料完整性测试 | 45 项通过 |
| 原制造业演示发布配置及评分边界回归 | 36 项通过 |
| 本轮定向 pytest 合计 | 81 项通过，0 失败，0 跳过 |
| 首期只读校验 | 13 份资料、5 条规则、8 张工单、40 题、12 个关键用例绑定通过 |
| 通用套件完整性 | 120 题 verified，变动列表为空 |
| 模型调用 | 0；未执行问答质量实测 |

复现命令均从仓库根目录执行：

```powershell
.venv/Scripts/python.exe scripts/manufacturing_phase1_workflow.py check
.venv/Scripts/python.exe -m pytest tests/test_manufacturing_workorder_contracts.py tests/test_manufacturing_workorder_profile.py tests/test_manufacturing_demo_profile.py -q -o 'addopts='
.venv/Scripts/python.exe scripts/evaluate.py check
```

本机测试 XML 保存在被忽略的 `tmp/manufacturing-phase1-m0/tests.xml`。上述通过结果仅覆盖 M0 与相应旧行为，不代表 40 题答案、20 项运行权限、12 项接口故障、8 项页面会话或首期整体验收已经通过。

## M1 交付

[独立模拟 MES](../services/mock_mes/README.md)提供健康检查、授权后分页和单工单上下文读取。凭据来自服务端环境，源服务独立校验租户和允许工厂，未知或不可见工单统一 404。游标签名绑定业务范围、页大小及夹具；不提供公开修改、故障注入或生产执行接口。

`MESConnector` 只读取固定 GET 路径，关闭重定向、环境代理和自动重试，限制连接 2 秒及整次调用 5 秒。响应限制为 64 KiB，工单上下文最多 4,000 字符；拒绝压缩、截断、重复 JSON 字段、重复记录身份、非法类型及跨范围来源。源修订变化返回 409，同修订正文变化返回 502，超时及源认证失败返回脱敏的 503。

`BusinessAuthorizationService` 从当前有效用户、Keycloak `sub`、源系统和显式工厂授权生成 `BusinessScope`；不从知识库管理员或部门身份推导业务权限。`WorkOrderAccessService` 在调用前后鉴权，源失败也执行后置检查。

`provision_business_access.py` 默认只预演。显式应用时先检查全部身份，使用租户行锁串行化授权，追加审计并在同一事务提交；重复应用幂等，审计失败回滚。`view/review` 分开维护；撤回读取时清除审核权限，也能单独撤回审核、保留读取。

新增迁移 `d8f0b2c43569` 接续 `c7e9a1b32458`，建立业务授权、会话业务绑定、回答业务范围三张表。复合外键限制同租户关系，撤销授权不删除历史范围，非空业务数据阻止直接降级。当前尚未向既有业务库应用迁移，也未往既有会话或回答写入业务绑定。

## M1 验证记录

| 检查 | 结果 |
| --- | --- |
| 末轮 M1 定向检查，含真实回环 HTTP、服务退出、配置错误脱敏及数据库防误用测试 | 71 项通过，0 失败，0 跳过 |
| 独立 PostgreSQL 完整迁移及新旧兼容检查 | 9 项通过，0 失败，0 跳过；5 项新增、4 项既有质量闭环迁移检查 |
| 广泛非集成后端回归 | 1705 项通过，1 项既有 PostgreSQL 记忆测试按原显式开关跳过；58 项集成测试未选入 |
| 原件与冻结配置 | 首期 13 份资料、8 工单、40 题检查通过；通用 120 题 verified、变动列表为空 |
| 授权配置预演 | 显式 `apply=false`、`database_accessed=false` |
| 运行边界 | 模型调用 0；既有业务数据库、容器和索引均未更新 |

广泛回归与末轮 M1 定向检查有重叠，不能相加称为独立测试数量。广泛回归后补充的严格来源校验、并发授权保护和启动配置错误脱敏，由末轮定向测试及独立 PostgreSQL 检查覆盖。

PostgreSQL 16.15 的两轮验证使用新建随机命名的独立数据库；末轮从空库升级到 `d8f0b2c43569`，验证约束、旧数据保留、非空降级保护和并发幂等，测试库均已清理。未在演示业务库建立测试 schema 或运行升级。

中间一轮真实 HTTP 测试因 Windows Proactor 的连接清理异常导致服务退出检查失败；业务断言当时已通过。测试服务及客户端改用 Selector 事件循环并保留退出时限，末轮全部通过。原失败报告保留，不替换为通过记录。

本机证据均位于忽略目录：

- `tmp/manufacturing-phase1-m1/nonintegration-tests.xml`：广泛回归。
- `tmp/manufacturing-phase1-m1/m1-final-tests.xml`：保留的中间失败轮次。
- `tmp/manufacturing-phase1-m1/m1-final-r2-tests.xml`：修正回环服务退出后的 70 项检查。
- `tmp/manufacturing-phase1-m1/m1-final-r3-tests.xml`：末轮 71 项定向检查。
- `tmp/manufacturing-phase1-m1/postgres-r1/`、`postgres-r2/`：各轮迁移日志、JUnit、创建及清理报告。

M1 复现入口：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
.venv/Scripts/python.exe -m pytest tests/test_business_scope.py tests/test_mock_mes.py tests/test_mes_connector.py tests/test_mes_access_http.py tests/test_manufacturing_m1_runner.py -q -o 'addopts='
.venv/Scripts/python.exe scripts/run_manufacturing_m1_tests.py --create-test-database --output-dir tmp/manufacturing-phase1-m1/新的数据库验证批次
.venv/Scripts/python.exe scripts/provision_business_access.py --config config/manufacturing_business_grants.example.json
```

独立测试入口限制本机与测试库名称，已有报告目录不能覆盖；连接凭据仅在进程内和测试子进程环境中使用。源配置、手动启动、权限维护和迁移说明见[模拟 MES 运行说明](../services/mock_mes/README.md)。

## M2 接续要求

以 M0 的待审核元数据和原文锚点为输入，建立可审核的制造适用性目录；不能把离线标注自动视为已发布规则。按实际开工时间、工单书面例外和冲突状态选择版本，保留旧指导书与在制沿用 ECN 的组合证据。

规则读取、候选筛选、父块和邻块回取、补查及证据刷新均须受知识库权限和制造适用范围约束。继续保留原有普通问答行为及冻结评分；不加载题集参考答案来决定业务选版。M3 再把业务访问服务、规则、生成、会话、快照和页面贯通。
