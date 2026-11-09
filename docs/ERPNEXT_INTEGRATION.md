# ERPNext 首期只读接入

本次在 `codex/erpnext-readonly-phase1` 分阶段实施：E0 独立部署；E1 只读适配与来源映射；E2 当前平台集成和权限/来源验证。范围为工单、工序、实际开工及支持这些事实的源身份；不开放修改、报工、放行或执行工具，不发起付费问答评测。

## E0 独立环境

固定 ERPNext `v16.54.0`，配置依据官方 [frappe_docker](https://github.com/frappe/frappe_docker/tree/46c7ced81efbd4a119e14b81bf78e3b77cad3309)。[Compose](../deploy/erpnext/compose.yaml) 使用独立项目 `manufacturing-erpnext`、MariaDB、Redis、站点/日志/队列持久卷，网页只绑定本机端口。初始密码由[准备脚本](../scripts/prepare_erpnext_environment.py)随机生成到忽略的 `tmp/`，不使用公开默认口令。

```powershell
.venv/Scripts/python.exe scripts/prepare_erpnext_environment.py --output-dir tmp/本轮ERPNext/deployment --apply
docker compose --env-file tmp/本轮ERPNext/deployment/deployment.env -f deploy/erpnext/compose.yaml up -d db redis-cache redis-queue configurator
docker compose --env-file tmp/本轮ERPNext/deployment/deployment.env -f deploy/erpnext/compose.yaml run --rm create-site
docker compose --env-file tmp/本轮ERPNext/deployment/deployment.env -f deploy/erpnext/compose.yaml up -d backend websocket worker scheduler frontend
```

初始化只对新站点执行，已有站点拒绝重建。停止使用 `docker compose ... stop`，保留卷与记录；不执行 `down -v`。部署验证须记录实际镜像摘要、安装版本、登录/API、持久性与端口隔离；这不是工厂生产上线。

## E1 数据与来源契约

ERPNext 通过固定的 Work Order REST 资源读取；专用 API 用户只具有必要读取权限，工单范围还受公司授权约束。平台仍使用现有 `WorkOrderRecord` 与 MES 只读接口，新增适配服务完成认证及字段转换，不让普通请求选择 ERP 地址或凭据。

| 平台字段 | ERPNext 来源与约束 |
| --- | --- |
| 工单编号 | `Work Order.name`，原样保留 |
| 产品型号 | `production_item` |
| 工序 | `operations` 中唯一工序的 `operation`；多工序不擅选，保持未知并要求明确上下文 |
| 实际开工 | `actual_start_date`；不使用 `planned_start_date` 或报工日期替代 |
| 时间精度 | 保留日期/时间精度，时间戳按明确配置的站点时区转换 |
| 工厂范围 | 服务端公司到工厂映射，结合 ERP 用户权限和平台业务授权 |
| 源修订/更新时间 | 工单记录的稳定内容、上游修改时间及映射配置决定，不混入本次采集时间 |

未接入的路线修订、批次和变更通知保持未知；不从相似字段推定。E1 的兼容入口不自动选择多工序；E3 增加显式工序和作业卡选择，仍不能把工单级开工时间标为任意工序的实际开工。

ERPNext 的 `Work Order.actual_start_date` 由其作业卡流程回写；首期仅读取这个已登记的工单字段。作业卡计时已经开始但尚未回写工单时，仍按源工单的空值处理，不把计划时间、当前时间或未提交的作业卡时间补为工单实际开工。

适配服务入口为 `services.erpnext_adapter.main:app`，配置样例见[字段与公司映射](../config/erpnext_adapter.example.json)、[平台源配置](../config/erpnext_sources.example.json)，部署覆盖文件见[适配服务 Compose](../deploy/erpnext/adapter.compose.yaml)。`ERPNEXT_API_KEY`、`ERPNEXT_API_SECRET`、`ERP_ADAPTER_READ_TOKEN` 只进入本机私有环境文件。平台源配置使用 `http://erpnext-adapter:8011`，仅候选 API 加入该私有网络。适配服务没有写路由，不暴露通用 ERP REST 代理，也不提供伪造的冻结夹具接口；ERPNext 来源尚未登记冻结评测时，质量样本审核/导出仍停止。

源配置的租户须与平台数据库映射一致；给 `erpnext-hc` 单独配置业务 `view` 授权，ERP 账号不能替代 Keycloak 身份和知识库权限。首期不授予该来源的质量 `review` 权限，不将模拟夹具登记复制成真实 ERP 登记。

## E2 验收要求

- 实际运行的 ERPNext 创建演示工单，通过真实 API 读取型号、单工序和实际开工；未开工保持空值。
- 平台业务授权及 ERP 用户只读权限同时生效；错误令牌、跨公司/工厂/租户、撤权及越权写入被拒绝。
- 计划开工不能补实际开工，多工序不能自动选第一项；来源改变和不可用阻止旧上下文继续交付。
- 沿用三次来源核验和原文/文档权限校验；服务端配置地址，凭据不进入响应、日志、快照或模型输入。
- 端到端使用独立候选端口和数据库；原 8000/8001/8002、冻结资料、评测结果及停止的质量/索引 Worker 保留。
- 实际模型调用为 0；模型替身与零预算验证明确标注，不宣称修复现有开发 13/16 或正式 31/40 的失败。

## 2026-11-09 分阶段交付与实测结果

E0～E2 已完成。结构化记录见[本轮报告](reports/erpnext-readonly-20261109-r1.json)；本机私有产物位于 `tmp/erpnext-phase1-20261109-r1/`，包括实际 API 响应、源码归档、各轮测试、故障夹具记录、零模型账本和备份。

| 阶段 | 实际完成情况 |
| --- | --- |
| E0 独立部署 | ERPNext 16.54.0 / Frappe 16.55.2，网页 `http://127.0.0.1:18080`；独立 MariaDB、Redis、站点卷与网络；前端单独配置本机端口网络，数据服务不暴露宿主端口 |
| E1 只读适配 | 通过真实 ERP API 读取 4 张演示工单，读取账号可见授权公司内 3 张；另一个公司的工单返回 403；创建、修改、删除请求均返回 403 |
| E2 当前项目集成 | 独立工作台 `http://127.0.0.1:8003`，新数据库 `erpnext_readonly_phase1`、独立文档副本、Redis 和 PKCE 客户端；沿用原演示登录身份，业务范围只在新库配置 |

ERPNext 管理员为 `Administrator`，随机密码保留在本机 `tmp/erpnext-phase1-20261109-r1/deployment/secrets/admin-password`；读取账号的 API 凭据仅在私有配置中。不要将这些文件提交到 Git。ERP 中创建的是演示数据，工单实际开工来自正常提交的作业卡流程，不能称为真实工厂执行记录。

演示工单包括 `ERP-WO-STARTED`（HC-A17 / OP-20，实际开工有值）、`ERP-WO-NOTSTARTED`（计划时间有值、实际开工为空）、`ERP-WO-MULTI`（多工序，平台不擅选）及另一公司的 `ERP-WO-OTHER`（读取账号不可访问）。网页可由管理员查看和维护原系统记录，平台适配服务只发 GET 请求。

平台末轮正常/权限检查 **16 项通过**，包括实际字段投影、业务引用、三次来源核验、快照、缺字段澄清、多工序澄清、执行请求拒绝，以及生成后撤销业务授权使交付返回 404。额外验证真实源修订变化返回 **409**、适配服务停机返回 **503**，故障夹具和授权均已恢复；部署后直接访问 8003 的上下文也与 ERP 原始记录一致，连续读取的源修订和哈希稳定。

问答环节明确使用确定性生成替身和零模型硬预算；真实的是认证、数据库、ERP HTTP 及来源/权限/快照链路。各轮保存了 51 条同进程 HTTP 观察，另有 2 条部署端口的上下文观察；对其中 41 个可观测请求 ID 核对数据库独立账本，全部观察 Trace 均无模型用量，新物理模型调用 **0**。无独立请求 ID 的记录是未认证请求和快照读取，不作为新增问答计数。这不是模型准确率或外部网关压测，不把接入检查合并到旧开发/正式成绩。

最终业务提交 `a5174272d86e1940317b3ff254d2ac28464f2659` 的候选镜像为 `enterprise-knowledge-manufacturing:erpnext-readonly-20261109-r3`，ID 为 `sha256:d7984272ce0c8042c554c28950e2b3eec832d75127c553204c08f3d5e1ed5082`。157 个业务 Python 文件及 4 个适配服务文件与 Git 归档一致；业务代码哈希 `9336ac89f58a3666918ec192ab7946f49c339ce793c1d9d325e72b4b434847e0`。原 8000/8001/8002 镜像保持，候选使用复制后的 13 份原件/53 个 SQL 分块，并以已有只读凭据访问同一演示集合的 53 个向量；未重建索引或启动候选知识库索引/质量 Worker。ERPNext 自己的队列 Worker 与调度器正常运行。

工程验证末轮为 **2213 项通过、1 项既有 PostgreSQL 开关测试跳过、95 项集成测试排除**；定向 69 项通过，通用 120 题与首期 40 题冻结完整性通过。部署使用真实 MariaDB/ERPNext，平台观察与撤权验证使用独立 PostgreSQL 候选库；本轮没有重跑旧的全部 PostgreSQL 集成测试。各轮重叠测试不相加作为样本数。

初次缺 ERP 主数据、前端内部网络端口与队列参数，以及验证脚本误用授权表主键/Bench 布尔参数的失败记录均保留。已通过官方初始化函数、部署配置和验证脚本修正，不放宽生产权限/来源规则，不覆盖旧结果。最终 ERP 后端重启后记录仍可读取；ERP 站点及候选 PostgreSQL 已备份，备份目录核对通过，未做完整恢复演练。

## E3 按工单选择工序与作业卡

工单列表对支持选择的来源返回 `selection_supported=true`；该能力由服务端源配置的 `execution_selection` 开启，旧 MES 默认关闭。工作台依次选择工单、具体工序、可选作业卡；切换父级会清除子级选择并开始新会话。历史会话保存原工序和作业卡身份，刷新不暗换对象。

工序使用 `Work Order.operations[].name` 子行编号定位，不能只传 `operation` 工序代码：同一工单可以重复执行相同工序。作业卡必须属于所选工单和该子行，且工序代码、产品、公司均一致；已取消的卡不可选。平台继续校验租户和工厂业务授权，ERP 用户继续受公司 `User Permission` 约束。读取账号仅新增 `Job Card` 的 read/select，写入、报工、提交、取消和放行均未开放。

| 时间层级 | 实际来源 | 用途 |
| --- | --- | --- |
| 工单实际开工 | `Work Order.actual_start_date` | 保持既有选版依据 |
| 所选工序实际开工 | 对应 `Work Order Operation.actual_start_time` | 回答该工序的已登记实际开工 |
| 所选作业卡实际开工 | 对应 `Job Card.actual_start_date` | 回答该卡的计时记录 |

三者独立展示，空值保持未知，不能互补，也不使用计划时间。ERPNext 的工单/工序实际时间与已提交卡有关；一张草稿卡可以已有计时记录而未回写父级，卡状态与开工时间同时保留，不能据此声称已报工完成。本轮简单的开工时间查询由后端直接投影已核验字段；复杂知识问答仍走原问答流程。

平台新增只读入口：

```text
GET /api/manufacturing/work-orders/{work_order_id}/operations
GET /api/manufacturing/work-orders/{work_order_id}/operations/{operation_id}/job-cards?limit=20&cursor=...
GET /api/manufacturing/work-orders/{work_order_id}/context?operation_id=...&job_card_id=...
```

创建会话和问答的请求可增加 `operation_id`、`job_card_id`，选卡必须同时指定工序；不接受浏览器传入实际时间等来源事实。工序列表上限 100，作业卡每页最多 20；游标签名绑定工单、工序、授权工厂、分页大小及父级修订。未提供选择时保留冻结的 `work-order-v1`；明确选择后使用独立的 `work-order-execution-v1`，不修改旧评测契约与历史字节。

每次问答的三次来源读取都保留同一选择。组合上下文包含父工单、选中工序及作业卡修订；父子分开读取时额外复核一次父级，来源已变返回 409，不可用返回 503。未选中卡的单独变化不进入当前卡的修订。交付前继续复核业务授权和文档范围。业务引用分别标明 `Work Order` / `Work Order Operation` / `Job Card`，使用对应记录编号、字段值和时间精度；知识引用仍由后端按原文与选版规则编译。

数据库迁移 `f2b8c0a961d3` 为 `conversation_business_bindings` 增加可空工序/卡编号，旧会话保持可读；同一会话不能换卡继续回答。存在这些绑定时降级迁移会拒绝丢失选择边界，回滚应用应先保留数据库并停止候选，不强制删除绑定。

在已完成 E0～E2 的专用站点，可复制最新[演示脚本](../services/erpnext_adapter/provision_demo.py)，调用 `frappe._knowledge_demo_setup.execution_demo` 创建新的 `ERP-WO-EXECUTION`，不重复初始化旧资料。此管理员工具只用于本机演示夹具，适配服务没有通往它的路由。新工单包含 OP-20、OP-30；OP-20 有一张已提交卡、一张较晚计时的草稿卡和一张时间未知的卡，供验证三层时间及来源变更。ERP 来源的质量审核/冻结评测仍未开放，旧开发和正式问答失败状态不变。

本轮实际部署版本、测试与零模型账本见后续实测记录；不能将上述功能范围当作模型准确率验收。

## 运行、复现与回滚

本机 ERP 启动参数在私有 `deployment/deployment.env`，候选 API 参数在私有 `candidate/compose.json`：

```powershell
docker compose --env-file tmp/erpnext-phase1-20261109-r1/deployment/deployment.env -f deploy/erpnext/compose.yaml -f deploy/erpnext/adapter.compose.yaml ps
docker compose -f tmp/erpnext-phase1-20261109-r1/candidate/compose.json ps
```

新环境先完成 E0 的新站点初始化，再用 ERP 页面建立公司和制造数据，或在本次专用 `erpnext.localhost` 站点使用[演示初始化脚本](../services/erpnext_adapter/provision_demo.py)。该脚本在 ERP 容器内通过 Frappe 正常文档 insert/submit 创建公司、BOM、工单、作业卡和只读用户；输入文件是私有 JSON，仅包含随机生成的 `api_key` / `api_secret`，复制至 `/tmp/erpnext-seed-input.json` 后执行：

```powershell
# 本机参数示例；新环境替换目录并准备新的私有输入文件，不覆盖已有数据。
$erpComposeArgs = @('--env-file', 'tmp/erpnext-phase1-20261109-r1/deployment/deployment.env', '-f', 'deploy/erpnext/compose.yaml')
docker compose @erpComposeArgs cp services/erpnext_adapter/provision_demo.py backend:/home/frappe/frappe-bench/apps/frappe/frappe/_knowledge_demo_setup.py
docker compose @erpComposeArgs cp tmp/erpnext-phase1-20261109-r1/erpnext-seed-input.json backend:/tmp/erpnext-seed-input.json
docker compose @erpComposeArgs exec -T backend bench --site erpnext.localhost execute frappe._knowledge_demo_setup.run
```

接入适配服务时，以实际租户/公司替换样例，配置专用只读用户凭据和平台源授权。新克隆的资料发布、Keycloak 映射与库授权仍须按[首期独立初始化说明](MANUFACTURING_QUALITY.md)建立；本机候选通过已审核演示库的一致性副本启动，不把私有 `tmp` 当作仓库默认数据或通用自动安装器。

回滚可停止 `erpnext-readonly` 候选和 `manufacturing-erpnext` 项目，保留新数据库、卷与记录；或将新候选的私有配置切回保留镜像。原 8000/8001/8002 不需要回滚，本轮未更新它们。最近真实模型开发 **13/16、关键 4/6** 和正式 **31/40、关键 9/12** 的失败状态仍保留，后续模型复验需另有明确范围。
