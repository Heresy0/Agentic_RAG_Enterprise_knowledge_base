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

未接入的路线修订、批次和变更通知保持未知；不从相似字段推定。多工序选择和作业卡粒度属于后续扩展，不能把工单级开工时间标为任意工序的实际开工。

ERPNext 的 `Work Order.actual_start_date` 由其作业卡流程回写；首期仅读取这个已登记的工单字段。作业卡计时已经开始但尚未回写工单时，仍按源工单的空值处理，不把计划时间、当前时间或未提交的作业卡时间补为工单实际开工。

适配服务入口为 `services.erpnext_adapter.main:app`，配置样例见[字段与公司映射](../config/erpnext_adapter.example.json)，部署覆盖文件见[适配服务 Compose](../deploy/erpnext/adapter.compose.yaml)。`ERPNEXT_API_KEY`、`ERPNEXT_API_SECRET`、`ERP_ADAPTER_READ_TOKEN` 只进入本机私有环境文件。平台源配置使用 `http://erpnext-adapter:8011`，仅候选 API 加入该私有网络。适配服务没有写路由，不暴露通用 ERP REST 代理，也不提供伪造的冻结夹具接口；ERPNext 来源尚未登记冻结评测时，质量样本审核/导出仍停止。

## E2 验收要求

- 实际运行的 ERPNext 创建演示工单，通过真实 API 读取型号、单工序和实际开工；未开工保持空值。
- 平台业务授权及 ERP 用户只读权限同时生效；错误令牌、跨公司/工厂/租户、撤权及越权写入被拒绝。
- 计划开工不能补实际开工，多工序不能自动选第一项；来源改变和不可用阻止旧上下文继续交付。
- 沿用三次来源核验和原文/文档权限校验；服务端配置地址，凭据不进入响应、日志、快照或模型输入。
- 端到端使用独立候选端口和数据库；原 8000/8001/8002、冻结资料、评测结果及停止的质量/索引 Worker 保留。
- 实际模型调用为 0；模型替身与零预算验证明确标注，不宣称修复现有开发 13/16 或正式 31/40 的失败。

实际阶段结果与复现证据完成后补记，不以本方案代替验收。
