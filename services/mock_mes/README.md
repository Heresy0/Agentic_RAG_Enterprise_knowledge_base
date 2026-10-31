# 首期只读模拟 MES

本服务属于 M1，使用[首期工单夹具](../../sample_docs/manufacturing_workorder_v1/README.md)提供经过源调用凭据、租户和工厂校验的只读列表与工单上下文。它独立于 8000 稳定展示及 8001 候选，不提供生产执行、报工、质量放行或公开数据修改接口。

M1 同时提供 `MESConnector`、`BusinessAuthorizationService` 和 `WorkOrderAccessService`。用户端制造问答、工单选择页面及历史业务证据的读写将在 M3 接入；当前普通知识问答不会自动调用本服务。

## 本机启动

在仓库根目录，使用同一个终端配置源服务和未来调用端共用的只读凭据。下列命令将凭据保存在进程环境中，不显示或写入配置文件：

```powershell
$env:MOCK_MES_READ_TOKEN = .venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(40))"
$env:MANUFACTURING_SOURCES_FILE = (Resolve-Path config/manufacturing_sources.example.json).Path
$env:MOCK_MES_FIXTURES_FILE = (Resolve-Path sample_docs/manufacturing_workorder_v1/work_orders.json).Path
.venv/Scripts/python.exe -m uvicorn services.mock_mes.main:app --factory --host 127.0.0.1 --port 8010 --no-access-log
```

示例使用固定合成租户及 HC-P01、HC-P02 两个工厂。实际应用须在服务端配置中登记对应源系统、租户和工厂，并为具体用户登记独立业务权限。示例中的工单身份不与现有演示账号自动绑定。

源配置只保存 `token_env` 引用。没有凭据、凭据格式不符、租户或源系统不匹配时启动失败，不使用默认密钥。M1 每租户只配置一个源系统；HTTP 明文地址限于本机回环与 `mock-mes` 容器服务名，其他地址要求 HTTPS，地址不能包含用户密码、查询串、片段或路径前缀。

## 源接口

| 接口 | 行为 |
| --- | --- |
| `GET /health` | 仅返回服务状态和合成只读模式 |
| `GET /v1/work-orders?limit=20&cursor=...` | 授权后分页，最多 20 条；不返回全库总数 |
| `GET /v1/work-orders/{id}/context` | 返回白名单工单字段；未知或不可见工单统一 404 |

业务读取需要 `Authorization: Bearer ...`、`X-MES-Tenant` 和 `X-MES-Plants`。这些是平台服务端产生的源调用信息，不向普通用户暴露。源服务独立检查凭据的最大租户和工厂范围，再按本次请求范围过滤数据，最后分页。

游标经过签名，绑定租户、源系统、工厂范围、夹具内容及页大小。篡改游标或改变范围后复用游标返回 422。源服务没有模型、题集或标准答案依赖；故障与修订变化使用测试替身和独立测试实例注入，不增加公开的修改接口。

## 连接器约束

连接器只允许固定 GET 路径，关闭重定向、环境代理和自动重试。连接上限 2 秒，整次调用上限 5 秒；在响应头、流式正文和解析阶段检查边界。单响应最大 64 KiB，单工单序列化上下文最多 4,000 字符，拒绝压缩响应、截断、重复 JSON 字段、重复来源记录身份和非法结构。

HTTPX 的读取超时针对单个数据块，因此额外使用 `asyncio.timeout` 限制整次调用；流式正文在退出或取消时关闭。实现依据见 [HTTPX 超时说明](https://www.python-httpx.org/advanced/timeouts/)及[异步流式读取说明](https://www.python-httpx.org/async/)。

| 来源情况 | 平台服务层错误 |
| --- | --- |
| 不存在或不可见 | `MESNotFound`，404 |
| 无法连接、超时、源认证失败、429 或 5xx | `MESUnavailable`，503 |
| 非法结构、身份不符、超限、截断、异常重定向 | `MESError`，502 |
| 与预期源修订不同 | `MESContextChanged`，409 |
| 同修订下字段或指纹发生变化 | `MESError`，502 |
| 分页游标无效或范围变化 | `MESInvalidCursor`，422 |

源错误正文、凭据及原始请求头不进入错误信息。`WorkOrderAccessService` 从当前数据库身份和授权计算范围，在源调用结束后再次鉴权，包括源调用失败的情况。旧知识库管理员、部门负责人身份均不自动取得业务读取或审核权限。

## 业务授权配置

先按[配置样例](../../config/manufacturing_business_grants.example.json)填写目标租户、实际用户 ID、Keycloak `sub`、源系统和工厂。工具只操作既有且有效的用户，不创建租户或账号，不改变已有身份绑定。

默认预演不读数据库：

```powershell
.venv/Scripts/python.exe scripts/provision_business_access.py --config config/manufacturing_business_grants.example.json
```

应用经过核对的配置时，显式指定目标连接环境变量；不要把连接 URL、密码或令牌放在命令参数中：

```powershell
.venv/Scripts/python.exe scripts/provision_business_access.py --config tmp/本期业务授权.json --apply --database-url-env MANUFACTURING_TARGET_DATABASE_URL
```

`view` 与 `review` 分开登记。授予审核时必须显式包含读取权限；撤回时可以仅撤回 `review`，保留 `view`。撤回读取权限时同时清除相应审核授权，避免重新授予读取后意外恢复旧审核权限。撤回命令使用 `--operation revoke --apply`。

全部用户和 `sub` 在写入前校验。租户行锁串行化并发授权，重复执行幂等；授权和审计在同一事务提交，审计失败回滚。审计将本机数据库管理员工具标为 `local_admin_cli`，不冒用某个已登录用户的身份。

## 数据库迁移与回归

新增迁移 `d8f0b2c43569` 接续 `c7e9a1b32458`，建立 `business_scope_grants`、`conversation_business_bindings` 和 `qa_run_business_scopes`。后两张表供 M3 写入会话与回答范围，M1 尚不往既有问答历史增加业务数据。

复合外键绑定同租户的用户、会话和回答。撤销授权不会级联删除历史范围；非空授权或业务绑定存在时，迁移拒绝直接降级，须先完成数据导出和安全迁移。本阶段不在既有演示数据库执行该迁移。

以下入口显式创建随机命名的本机独立测试数据库，运行从空库到 head 的完整迁移、新旧 PostgreSQL 检查，最后只清理本次创建的数据库：

```powershell
.venv/Scripts/python.exe scripts/run_manufacturing_m1_tests.py --create-test-database --output-dir tmp/manufacturing-phase1-m1/新的数据库验证批次
```

管理连接优先读取 `MANUFACTURING_ADMIN_DATABASE_URL`，否则按需读取本机 `.env` 中的数据库连接；仅接受本机地址。凭据只在进程环境中传给测试子进程，不写入报告。已有独立测试库可以通过 `MANUFACTURING_TEST_DATABASE_URL` 提供，此时工具不会删除该数据库。

测试库名称必须符合 `manufacturing_m1_test_` 加 12 位十六进制后缀；普通业务库、远程地址和已有报告目录会被拒绝。测试产物保存在忽略的 `tmp/`，包含脱敏迁移日志、JUnit 和数据库创建清理记录。

无数据库与无模型的来源完整性检查仍使用：

```powershell
.venv/Scripts/python.exe scripts/manufacturing_phase1_workflow.py check
.venv/Scripts/python.exe scripts/evaluate.py check
```
