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
| M2 适用性 | 实现及隔离验证通过 | 制造标注草稿/复核/发布、确定性选版、SQL 必需证据及所有取证路径的制造范围 |
| M3 问答与页面 | 待实施 | 制造 HTTP 入口、工单与文档两类证据、会话绑定与页面 |
| M4 闭环与验收 | 待实施 | 业务样本审核、冻结业务输入的 Worker、正式实测、权限及故障矩阵、部署与演示 |

当前可以独立运行 M1 只读模拟 MES，并通过服务层按数据库授权读取工单；M2 提供基于已复核 SQL 目录的选版结果和制造检索范围。普通问答仍不自动调用 MES，制造用户端问答和页面将在 M3 接入。后续阶段继续沿用本会话的实施授权；每阶段完成后记录实际检查和剩余项。

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

## M2 交付

[制造适用性说明](MANUFACTURING_APPLICABILITY.md)记录完整接口、字段、规则、时间依据和接入边界。运行期模型独立于 M0 冻结契约，不从题集或标准答案读取选版结论。

新增制造元数据 GET/PUT，沿用 `editor` 权限，使用技术版本和标注修订并发检查、租户/文档行锁与同事务审计。先保存草稿，再复核相同标注，最后走现有发布接口。制造修改令审核及发布失效；发布重新校验当前审核和 SQL 来源。正文预览提供分块指纹、索引代次以及可选父块窗口，便于真实审核。

`ApplicabilityResolver` 只接受可信工单上下文、基础知识库范围和独立业务范围。实际开工日期决定一般版本；报工日期、提问日期和“最新”表述不参与替换。有效书面工单例外优先；无明确替代、循环替代、文件身份或检验规范重叠均返回冲突。缺字段、未知路线、不可见通知或无已复核依据不会开放正文检索。

`ManufacturingFence` 将选定文件、技术版本、原件指纹、制造修订及审核摘要传到关键词、向量、重排、Agent search/read、邻块、父块、正文补取、反馈补查和证据刷新。向量和 SQL 都在 top-k/LIMIT 前约束文档；决定选版的指导书、ECN/通知及检验锚点直接从 SQL 取得，不依赖它们进入候选排名。制造模式保留“归档旧指导书＋后来生效的在制沿用 ECN”，普通问答保留既有时间处理。

每次取证复查目录及来源；新增通知、撤回、换版或原文变化使整个选版失效。`ManufacturingScopeChanged` 穿透 Agent 和反馈补查，不能降级复用旧答案。处理上限为 256 份目录资料、64 条相关规则、32 份适用文件、24 个必需锚点和 32 个证据分块；超限要求缩小范围或复核。

M2 复用现有 JSON 容器，不新增迁移。没有向原演示库写入制造标注、发布 M0 草稿或更新部署。

## M2 验证记录

| 检查 | 结果 |
| --- | --- |
| 末轮定向检查：元数据 API、选版、所有取证路径、补查失效及数据库入口保护 | 143 项通过，0 失败，0 跳过 |
| 独立 PostgreSQL：完整迁移、真实 JSON/BM25、并发审核、跨会话撤回及发布 | 10 项通过，0 失败，0 跳过；末轮测试库已删除 |
| 广泛非集成后端回归 | 1776 项通过，1 项既有 PostgreSQL 记忆测试按原显式开关跳过，70 项集成测试未选入 |
| 原件与冻结配置 | 首期 13 份资料、5 条规则、8 工单、40 题及 12 个关键用例绑定通过；通用 120 题 verified，变动列表为空 |
| 运行边界 | 模型调用 0；既有演示业务库、容器、索引及原始评分文件均未更新 |

各轮检查有重叠，不能相加作为独立测试数量。广泛回归期间收紧的规则遍历预算及父子窗口一致性，由末轮 143 项定向检查覆盖。PostgreSQL 两轮均为随机命名的本机独立数据库，从空库升级到 `d8f0b2c43569`，完成后清理；未在演示业务库建立测试 schema。

最初兼容性检查发现新增范围的前向类型引用不能被 LangChain/Pydantic 解析；改为运行期可解析的类型后，通过定向及广泛回归。原失败报告保留。

本机证据位于被忽略的 `tmp/manufacturing-phase1-m2/`：

- `initial-compatibility-r2.xml`：保留的最初类型解析失败轮次。
- `metadata-selection-r1.xml`：51 项审核、选版及兼容性检查。
- `retrieval-r1.xml`：174 项取证路径与原有行为检查。
- `m2-targeted-r2.xml`、`m2-final-r3.xml`：中间通过轮次，分别为 140、141 项。
- `m2-final-r4.xml`：末轮 143 项定向检查，包含同一锚点子块/父块窗口及多子块共享父块回归。
- `nonintegration-r1.xml`：广泛回归。
- `postgres-r1/`、`postgres-r2/`：各轮完整迁移日志、10 项 JUnit 和创建/清理报告。

M2 复现命令见[制造适用性说明](MANUFACTURING_APPLICABILITY.md)。M1 数据库测试入口新增内部阶段选择后仍默认执行原 M1 检查；M2 包装入口使用独立的 M2 名称保护，拒绝业务库、远程库和 M1 测试库。

## M3 接续要求

将 M1 工单读取、M2 选版与制造 HTTP 问答入口贯通，保留原普通问答入口。制造请求只接受工单及问题，不接受客户端覆盖业务事实。调用前后检查业务和文档权限，源修订变化停止回答；制造目录失效需提示重新查询，不能把异常变成旧答案。

完成工单与文档两类证据、有限业务快照及诊断、会话工单绑定、历史访问限制和工作台页面。缺条件、无证据和冲突先形成有依据的澄清/拒答；页面不能绕过选版直接查询全库。正式 40 题问答质量、业务反馈闭环、权限与故障矩阵和部署留给既定后续验收，不将本轮逻辑测试计作模型准确率。
