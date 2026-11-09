# OpenMES 独立部署与首期只读接入

本阶段分为 O0 独立部署、O1 只读适配、O2 平台执行查询。范围为工单、生产批次、工序步骤及各自实际开工，保留 ERPNext 和原应用环境。自建 MES 中建立的是演示记录，不视为真实工厂执行证据。

## 来源与权限

采用官方 [OpenMES 0.28.0 源码](https://github.com/Mes-Open/OpenMes/tree/eb184a2984b74d685e65ee24df9b8c7b34c765f6)，提交 `eb184a2984b74d685e65ee24df9b8c7b34c765f6`；官方镜像固定为 `ghcr.io/mes-open/openmes@sha256:0b64934615eaa60697777ed9d18423eb91bc38b27ea1e0cbba3e326f74cc94fe`。上游核心使用 AGPL-3.0，新增上游中间件和演示初始化脚本在源码中公开，标注 AGPL-3.0-or-later；上游完整源码和许可证仍以该固定仓库为准。

上游普通 Operator 角色可以执行步骤，仅建立操作员令牌不足以形成只读边界。本项目增加[只读令牌中间件](../deploy/openmes/ReadOnlyIntegration.php)：具有 `knowledge:read-only` 能力的专用 Sanctum 令牌，仅允许 GET 工单列表/详情、工单批次和批次详情；写入、令牌刷新和其他接口均拒绝。原认证、令牌撤销、产线分配和工单访问策略保留，管理员通过原系统维护演示数据。适配服务另使用独立源令牌、平台租户和工厂范围，不向浏览器传递上游令牌。

源地址、上游租户及“真实产线 ID → 平台工厂”映射仅由服务端配置决定，见[适配配置样例](../config/openmes_adapter.example.json)和[平台配置样例](../config/openmes_sources.example.json)。平台仍通过 Keycloak 和数据库中的业务 view 授权检查访问，仅读取执行字段不要求提升知识库管理员或业务 review 权限。

## 数据层级与时间语义

OpenMES 的结构为“工单 → 批次 → 步骤”，使用独立 `openmes-execution-v1` 查询契约。批次不作为 ERPNext 作业卡，步骤也不作为任意工单级工序；父子实体按真实 ID 绑定，不能通过名称或序号自动选择。

| 层级 | 实际来源 | 本期行为 |
| --- | --- | --- |
| 工单 | `WorkOrder` | 固定版本无工单实际开工字段，显示未知；不取计划日期或最早批次时间 |
| 批次 | `Batch.started_at` | 读取该批次已登记时间，空值保持未知 |
| 步骤 | `BatchStep.started_at` | 读取所选步骤已登记时间，空值保持未知 |

批次号、批号、步骤名称、状态、实际时间、时间精度和源更新时间保留。源时间须有明确时区；上游站点配置 Asia/Shanghai，JSON 来源的 UTC 时间仍按其精度保存。开工记录不能证明检验、签署、报工完成或质量放行。

版本判断和已有工单问答仍使用原有工单级来源；该 OpenMES 版本缺少工单实际开工和唯一工序时如实澄清。本期批次/步骤为确定性字段查询，没有把选中批次偷偷用于工单选版，也未增加批次绑定的模型问答、会话或质量样本审核。那些能力需后续明确契约后再接入。

## 接口与核验

沿用鉴权工单列表和工单上下文；新增：

```text
GET /api/manufacturing/work-orders/{work_order_id}/batches
GET /api/manufacturing/work-orders/{work_order_id}/batches/{batch_id}/steps
GET /api/manufacturing/work-orders/{work_order_id}/execution-context?batch_id=...&step_id=...
```

选择步骤必须指定批次，外来工单/批次/步骤拒绝。执行上下文在返回前读取两次并比较完整白名单记录和配置哈希，每次异步读取前后检查当前业务授权；与原工单问答三次校验分别记录。预期修订不匹配或读取中来源变化返回 409，来源不可用返回 503，业务无权或不存在返回 404；不回退至旧快照。字段引用标明 `Work Order`、`Batch`、`Batch Step`、真实记录 ID、字段值及本次来源修订。

工作台选择工单后显示独立“MES 执行记录”区域，依次选择批次、步骤并重新核验。字段使用文本渲染；切换工单、退出登录、来源变化或权限失败时清空执行视图，晚到响应不能恢复旧数据。下方知识问答仍明确按工单范围查询。

每个租户仍只允许一个业务源。本机用新的候选数据库、配置、文档卷及浏览器 PKCE 客户端接入 OpenMES，ERPNext 环境继续独立保留；本期不实现 ERPNext 与 OpenMES 的工单同步或自动关联。

## 部署与复现

独立[Compose](../deploy/openmes/compose.yaml)包含 PostgreSQL、OpenMES 后端、WebSocket 和前端，数据库/后端不暴露宿主端口，页面仅绑定本机 18081，设置独立内存限额、持久卷，关闭可选遥测。首先生成私有凭据：

```powershell
.venv/Scripts/python.exe scripts/prepare_openmes_environment.py --output-dir tmp/新的OpenMES/deployment --image enterprise-openmes:readonly-0.28.0-r1 --upstream-image ghcr.io/mes-open/openmes@sha256:0b64934615eaa60697777ed9d18423eb91bc38b27ea1e0cbba3e326f74cc94fe --upstream-revision eb184a2984b74d685e65ee24df9b8c7b34c765f6
docker build -t enterprise-openmes:readonly-0.28.0-r1 deploy/openmes
docker compose --env-file tmp/新的OpenMES/deployment/deployment.env -f deploy/openmes/compose.yaml up -d
```

准备工具拒绝覆盖已有目录，密码随机生成，不使用公开默认值。现有站点不重复初始化；可在原 MES 页面建立产线、产品、工艺路线、工单和批次，或在全新本机站点使用[演示初始化脚本](../services/openmes_adapter/provision_demo.php)，它调用上游正常模型/业务服务，已有工单时拒绝运行。上游 API 凭据和专用读令牌只写私有文件。

适配器通过[覆盖 Compose](../deploy/openmes/adapter.compose.yaml)启动，平台镜像包含 `services.openmes_adapter.main:app` 工厂。新克隆可使用根 Dockerfile 构建；本机依赖下载失败时可用[依赖复用构建](../deploy/openmes/platform.Dockerfile)，但必须逐字节匹配基础镜像的 requirements.txt，不能为下载问题关闭 TLS 校验。当前本机配置入口见下面交付记录；私有 tmp 不是新克隆自动具备的配置。

回滚只停止新 OpenMES 或新候选，保留数据库和卷；不要执行 `down -v`。原 ERPNext、原应用端口及旧评测不需要回滚。

## 验证入口

```powershell
.venv/Scripts/python.exe -m pytest tests/test_openmes_adapter.py tests/test_openmes_api.py tests/test_erpnext_adapter.py tests/test_erpnext_execution.py tests/test_manufacturing_m3_api.py
node --test tests/frontend_conversations.test.cjs tests/frontend/auth.test.mjs
.venv/Scripts/python.exe scripts/evaluate.py check
.venv/Scripts/python.exe scripts/manufacturing_phase1_workflow.py check
```

在允许本机回环连接的环境执行异步测试。集成验收须记录真实源响应、上游写入拒绝、平台撤权、父子身份错配、源修订变化和来源停机；不以单元测试代替实际部署验证，也不以零模型字段查询计算模型准确率。本阶段不发起付费评测，不改变旧 ERPNext 10/16 或旧模拟开发/正式失败状态。

## 2026-11-09 实测与交付

O0～O2 已完成。OpenMES 位于 [18081](http://127.0.0.1:18081/)，新平台位于 [8004](http://127.0.0.1:8004/)，使用独立数据库 `openmes_readonly_phase1`、文档卷、Redis 和浏览器 PKCE 客户端；原 ERPNext 的 18080 / 8003 及原 8000 / 8001 / 8002 保留。候选仅给现有知识管理员配置 `openmes-hc / HC-P01 / view`，未增加业务 review、知识库或 ERPNext 权限。

业务提交 `af1c767f2d5a1b9f20cc4a8f3a6038c83aad02a4` 已从 Git 归档构建为 `enterprise-knowledge-manufacturing:openmes-readonly-20261109-r1`，镜像 ID `sha256:987e85406dabc57cabca4a7a33e5eba7a33daa0fe296640788654823fba49e94`；API 和适配器的 298 个文件逐字节匹配归档，其中后端 Python 163 个。OpenMES 增强镜像 ID 为 `sha256:171e1a452a5e22dbb6a53d944bb59e8b7a81a170fe759e9149a8df90229d1969`，11 个上游关键文件及扩展与固定源码核对一致，bootstrap 只增加专用令牌限制。

通过正常上游模型和业务服务创建 3 张演示工单、6 个批次、12 个步骤；读取用户仅分配一条产线，可见其中 2 张工单。首张工单第一批次第一步骤已开始，第二步骤和其他批次实际开始保持未知；工单级实际开工始终未知。演示管理员初始化和故障夹具维护与平台只读链路分别记录，不称为真实工厂数据。

上游 HTTP **8 项**、平台 HTTP **25 项**通过，包括跨产线/工单/批次/步骤拒绝、令牌写入/刷新拒绝、平台撤权、来源修订变化和来源停机。故障期间返回 409 / 503 / 404，恢复字段值仍保留新修订，不回填原更新时间。两项硬零预算问答分别验证执行请求拒绝及缺条件澄清，均有三次来源读取与快照；新增物理模型调用和 Token **0**，22 个可观测请求 ID 及整个独立候选用量账本核对一致。

完整非外部集成回归 **2305 通过、1 既有跳过**；专项 **113 通过**。之后补充字段引用清单，最终 OpenMES 定向 **23 通过**并完成上述真实链路验证；前端 **58 通过**。原 120 题及 40 题契约完整性通过，4 项新旧端口健康和 4 项页面资源字节检查通过。8 个原容器的 ID / 镜像 / 启动时间不变，15 份旧报告哈希一致；仍为 13 份原件、53 分块，未重建索引，未启动新候选索引或质量 Worker，质量任务 0。

首轮 Windows 沙箱异步 socket 阻塞、测试模块加载遗漏、来源摘要 UUID 序列化错误、TLS 依赖下载失败、新候选缺少只读向量凭据挂载，以及验收脚本误取旧扁平响应均保留对应记录。最终没有关闭 TLS、放宽认证或修改旧结果。新平台和 MES 数据库已备份且目录核验通过，未做完整恢复演练；本轮未做浏览器视觉检查。

当前私有配置、源码归档、备份、原始响应和 67 项证据清单在 `tmp/openmes-phase1-20261109-r1/`，凭据不提交。检查与停止入口：

```powershell
docker compose --env-file tmp/openmes-phase1-20261109-r1/deployment/deployment.env -f deploy/openmes/compose.yaml -f deploy/openmes/adapter.compose.yaml ps
docker compose -f tmp/openmes-phase1-20261109-r1/candidate/compose.json ps
# 需要回滚时只停止新候选/新 MES，保留卷和数据库。
docker compose -f tmp/openmes-phase1-20261109-r1/candidate/compose.json stop
docker compose --env-file tmp/openmes-phase1-20261109-r1/deployment/deployment.env -f deploy/openmes/compose.yaml -f deploy/openmes/adapter.compose.yaml stop
```

完整结构化结果见[OpenMES 首期验收报告](reports/openmes-readonly-20261109-r1.json)。以上为工程接入验收通过，旧 ERPNext 开发 **10/16、关键2/6**、旧模拟开发 **13/16、关键4/6** 和正式 **31/40、关键9/12** 的失败状态分别保留；本轮没有模型准确率、模型改进收益或真实改进闭环通过的结论。
