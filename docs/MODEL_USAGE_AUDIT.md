# 模型 Token 与成本审计

这里审计的是模型消耗的 token，不是登录 JWT。以前的问答成功/失败审计事件继续保留，两者职责不同。

## 采集范围与调用链

| 环节 | stage | 采集方式 |
|---|---|---|
| 回答生成 | answer_generation | 结构化输出保留原始 AIMessage，先读取 usage，再处理解析结果 |
| 有历史的追问改写 | conversation_rewrite | 同上；没有历史时不调用模型，也不制造审计调用 |
| 查询向量化 | query_embedding | 拦截 SDK 每次 create 响应 |
| 文档向量化 | document_embedding | 拦截每个实际请求批次；不按切片猜测分摊 |
| 重排 | rerank | 读取 JSON 的 usage，再校验重排结果 |

HTTP 请求、直接服务调用、本地评估、入库脚本和索引 Worker 都绑定请求局部上下文。单轮、同一轮多轮中的改写/检索/回答共用 request_id；Worker 额外记录 job_id 和 document_id。HTTP request_id 与 X-Request-ID 一致。跨库检索只保存一条调用，通过关联表连接所有参与范围的知识库。

Chat 和 Embedding 禁用了 SDK 隐式重试，重排未配置重试。Worker 的重新执行产生新的调用 UUID，关联原 job_id，实际再次向量化就会再次计量。当前实现针对同步调用；Embedding 的默认异步入口仍使用被审计的同步方法。

## 数据与准确性

迁移 `c9e1f3b5d708` 新增 `model_usage_events` 和 `model_usage_scopes`，不修改文档分块/向量，因此不需要重新入库。

一条 event 是一次实际调用尝试，不是一次业务问题。字段包括租户、用户、request_id、环节、供应商、模型、开始时间、状态、input/output/total tokens、估算成本、币种。details 白名单仅含任务/文档/会话 ID、供应商 request_id、耗时、异常类型、缓存输入/推理 token 和价格快照。

- 不存 API Key、JWT、HTTP 头、问题、回答、提示词、文档正文、模型隐藏思考或异常消息。
- 仅有 total_tokens 的响应保留总量，input/output 留空；缺失、无效或不一致的统计不记为零。供应商只提供输入/输出时可由这两个已报告数相加，来源标为 provider_components。
- 缓存输入属于 input，推理 token 属于 output，不能重复加到 total。
- 结构化解析失败仍可保留原始响应用量；连接失败/超时没有用量时记 error + null。业务后续拒答或校验失败不抹掉已经成功的模型调用。
- 调用前独立提交 pending；调用结束独立更新一次 success/error。进程被强制终止时保留 pending + 未知用量，不能把它当成零支出。
- 独立 PostgreSQL 会话的提交不会随业务事务回滚。SQLite 内存测试默认不自动开启此独立持久化，避免共享连接误提交业务事务。
- 写审计失败采用 fail-open：业务继续，输出不含敏感信息的告警日志和 write_failures 指标；此时可能缺行或留 pending。没有持久 outbox/对账补偿，不能宣称供应商账单级百分之百完整。需定期监控失败及长期 pending。

## 成本配置

`.env` 的 `MODEL_USAGE_PRICES_JSON` 默认为 `{}`：立即可记录 token；未配置价格时 estimated_cost 为 null，不是免费。

下面数字**仅为格式演示，并非任何模型实际报价**。按自己供应商、地区、模型版本、计费层级的合同/控制台价格替换，单位为每百万 token：

```dotenv
MODEL_USAGE_PRICES_JSON={"dashscope:YOUR_CHAT_MODEL":{"version":"contract-2026-10","currency":"CNY","input_per_million":"2","output_per_million":"10","cached_input_per_million":"0.5"},"dashscope:YOUR_EMBEDDING_MODEL":{"version":"contract-2026-10","currency":"CNY","total_per_million":"1"},"dashscope:YOUR_RERANK_MODEL":{"version":"contract-2026-10","currency":"CNY","total_per_million":"1"}}
```

key 的模型名须与当前 DASHSCOPE_MODEL / DASHSCOPE_EMBEDDING_MODEL / DASHSCOPE_RERANK_MODEL 一致。追问和生成用相同模型时共用价格。每项必须有 version、三字母币种，并选择 input/output 单价或 total 单价，不可混用；缓存命中已报告但未配置缓存价格时费用未知。未报告缓存细项时按普通输入单价估算，可能高估。分层/地域/免费额度需要你提供适用的费率，系统不会猜测。修改配置并重启后只影响新调用，旧 event 保留自己的快照。

成本分币种统计，不把 CNY 和 USD 相加。供应商账单为最终结算依据；本地估算不涵盖折扣、赠送额度、额外工具费等所有计费因素。

## 查看入口

管理员使用现有登录 Bearer JWT 调用（不是模型 API Key）：

```text
GET /api/knowledge-bases/{knowledge_base_id}/model-usage
GET /api/knowledge-bases/{knowledge_base_id}/model-usage/summary
```

默认最近 7 天；可传 since/until（带时区 ISO 时间，最多 90 天）、request_id。明细 limit 为 1–100；下一页使用响应的 next_cursor 中 before 与 before_id，且固定本次响应的 since/until。

权限过滤先于 LIMIT 和聚合：只能查看本租户事件，必须管理事件关联的**所有**知识库。仅管理一个库的人看不到涉及其他库的跨库调用，即使自己能阅读那些库。知识库被删除或权限被撤销时不会自动扩大可见范围。接口返回的是目前可见事件，不代表不可见事件不存在。不同知识库视图可能包含同一个跨库 event，不能把各库汇总简单相加。

summary 按 stage/provider/model/currency/status 分组，包含 calls、reported_total_tokens（已知小计）、unknown_usage_calls、unpriced_calls 和 estimated_cost（已估价小计）。未知调用数必须与已知小计一起看。

Prometheus 已同时抓取 API 和 Worker。Grafana 新增 **Enterprise RAG · Model Token Audit** 面板：调用量、已报告 token、未知用量比例、分币种估算费用和审计写入失败。指标没有 tenant/user/request 等高基数标签。这是平台级运维面板，必须留在内网，不是租户权限隔离的账单。CLI 评估不会自动被这两个服务的抓取覆盖；进程重启/采集遗漏也会影响趋势，持久明细请查 PostgreSQL/API。

## 评估报告

原有完整题集命令不变。新一轮本地实测会把每题/每轮的用量放在 report.json 内，并在 report.md 新增“模型 Token 与成本审计”。即使后续环节失败，前面已经发生的用量也保留。不要求启用 --save-traces 才能统计。

顶层 model_usage 汇总物理 event_id 去重后的用量，不把 retrieval/rerank/context/answer 同一条轨迹计四遍。关键字段：

- calls：观测到的实际尝试数。
- reported_total_tokens：已知总量的小计，不是包括所有未知调用的完整支出。
- unknown_usage_calls / unknown_usage_rate：有调用但缺少可信用量的数量/比例；零调用时比例为空。
- unpriced_calls：无法估价的数量。
- estimated_cost_by_currency：按币种分开的已估价小计。
- unpersisted_calls：本地已观测但数据库保存未成功的调用数。
- observed_requests / unobserved_requests：有/无审计观测的题或轮次数。

旧报告/旧 trace 没有数据就标 observed=false，不补造历史花费。重放展示历史用量，不意味着本次又付费。HTTP 多轮评估没有把数据库审计拉取进报告，其报告用量仍是未观测；可用对应 X-Request-ID 查询管理员接口。仅测 parsing/index/security 或 HTTP 性能时，没有本地模型轨迹则不报告 token 为零。

本地评估的业务文档/向量仍只读，但新用量事件会独立持久化；多轮评估原本仍会创建并清理自身临时会话。新的审计表不会随这类会话清理而删除。

## 启用与验证

首次部署前备份数据库。API Compose 启动命令会先执行 alembic upgrade head；非 Compose 部署需自己运行迁移再启动新代码。缺失迁移时审计写入会告警并 fail-open，不代表正常完整审计。

```powershell
python -m alembic upgrade head
python -m pytest tests/test_model_usage.py -q
```

不需要重新上传文档，审计从启用后的下一次实际调用开始。离线回归验证用模拟用量，不调用付费模型；实际供应商是否返回完整 usage 仍需要在新的实测报告中确认。未知用量持续偏高时先检查供应商实际响应和 SDK 映射，不通过字符数假装得到真实账单 token。

专项真实 PostgreSQL 测试：设置 RUN_MODEL_USAGE_INTEGRATION_TESTS=1，再运行 `python -m pytest tests/test_model_usage_integration.py -q`。它在随机且严格校验命名的独立 schema 中执行迁移/回滚与模拟调用，结束仅删除这个测试 schema；不向生产审计表插入假账单，不调用模型。CI 已启用此检查。
