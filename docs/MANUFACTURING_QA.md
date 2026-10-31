# 制造问答、两类引用与工单会话（M3）

M3 将 M1 的只读工单访问、M2 的适用性判断和既有 RAG 主流程接到正式制造入口及同源工作台。真实 ERP/MES、正式 40 题质量评测、业务样本审核与 Worker、候选部署仍属于后续范围。本阶段测试使用隔离数据、模型及 Redis 替身，没有运行付费问答或更新已有演示容器。

源码入口：`backend/app/api/manufacturing.py`、`backend/app/manufacturing/service.py`、`answer_context.py`、`generation_models.py`、`conversations.py`、`history_access.py`。共享生成、修复和预算仍位于 `backend/app/knowledge/`，没有复制另一套 RAG 主流程。

## 接口

所有入口使用原 Keycloak 认证和当前数据库用户映射；业务读取不继承知识库管理员或部门负责人身份。制造问答需要有效知识库检索范围，只读工单列表和上下文由业务权限独立控制。来源配置及凭据仅由后端选择。

| 方法及路径 | 行为 |
| --- | --- |
| `GET /api/manufacturing/work-orders` | 授权后分页，`limit=1..20`、可选签名 `cursor`；不返回跨范围总数 |
| `GET /api/manufacturing/work-orders/{id}/context` | 当前可见工单白名单字段、时间精度、源修订、哈希和查询/核验时间 |
| `POST /api/manufacturing/conversations` | 校验工单与知识库后创建私有制造对话，固定工单身份、修订及内容哈希 |
| `GET /api/manufacturing/conversations` | 当前可读制造会话列表，支持 `knowledge_base_id`、`limit`、`offset`；过滤在分页前完成 |
| `GET /api/manufacturing/conversations/{id}` | 原快照历史，支持 `knowledge_base_id`、`limit`、`before`；不向 MES 查询当前值来覆盖历史 |
| `DELETE /api/manufacturing/conversations/{id}` | 当前授权下删除自有且未占用的制造会话；`knowledge_base_id` 与创建范围一致 |
| `POST /api/manufacturing/qa` | 制造问题、最多三次 MES 核验、带证据回答及原子保存 |

问答请求示例：

```json
{
  "question": "这张工单适用哪个指导书版本？",
  "work_order_id": "WO-018",
  "knowledge_base_id": null,
  "conversation_id": null,
  "expected_source_revision": "r1"
}
```

`knowledge_base_id=null` 仅表示当前授权的知识库集合。客户端不能额外传入源地址、租户、工厂、型号、实际开工日期、夹具版本或可信业务上下文。会话创建请求仅接受 `work_order_id`、`knowledge_base_id` 和可选预览修订。

成功响应继承普通 `answer/clarify/refuse`、文档 `citations` 和原生成耗时，扩展：

- `work_order_context`：当前用户可见的来源字段；未知路线保留 `null`，日期精度不补齐。
- `applicability`：状态、选定文件/业务修订、规则 ID、缺口、文档技术版本/审核修订及原文锚点。`comparison_only` 文件不能作为当前工单执行依据；冲突来源标记为 `conflict_evidence`。
- `business_citations`：独立业务编号、字段和值、源系统/记录/工单身份、修订、内容哈希、精度、采集及最后核验时间。工单不伪装成文档分块。
- `claim_bindings`：稳定结论序号、可见文本、结论类型、文档及业务编号的绑定。
- `run_id`、`snapshot_status`、`conversation_id`、`request_id`、`manufacturing_total_ms` 和 `manufacturing_timings`。

制造响应禁止缓存；错误统一附请求 ID。主要错误为：认证失效 401；工单不存在或业务不可见 404；知识库权限不足/变化 403；请求格式非法 422；会话占用、绑定/修订冲突或制造目录失效 409；源响应损坏、同修订正文变化或凭据回显 502；源、治理或生成服务失败 503；沿用原治理额度的 429。错误正文不包含源地址、凭据、原始供应商错误或未核验字段。

## 来源、生成与一致性

```mermaid
flowchart LR
    A[认证与当前授权] --> B[第一次读取工单]
    B --> C[会话校验与追问解析]
    C --> D[适用性目录与范围内检索]
    D --> E[第二次核验：主生成前]
    E --> F[共享生成、覆盖及引用校验]
    F --> G[第三次核验：交付前]
    G --> H[会话轮次、快照、范围与审计原子提交]
```

每次 MES 调用最多连接 2 秒、总调用 5 秒，不自动重试。第一次取得可信记录；第二次在检索和上下文构建之后、主答案生成之前执行；第三次在交付之前执行。澄清及确定性拒答也经过核验。修订或白名单内容哈希变化立即停止，不以新来源重启本轮生成；同修订的正文变化按源契约异常处理。

会话改写、规划、Agent、父块选择、答案生成、引用修复、schema 修复和再生成的每次模型调用前，都会复查当前业务/文档权限及相关制造目录。第二次核验跨同步生成线程返回异步 MES 客户端执行，没有创建额外源读取或绕开连接器截止时间。观察到撤权或目录失效的异常直接穿透相关组件，不降级交付旧答案。

最终核验后仍可能发生外部变化；响应和卡片明确给出最后核验时间，不承诺跨系统事务快照。平台仅查证及生成指导，不执行 MES/ERP 写入或生产放行。

## 两类来源的校验

文档仍使用 `资料1` 等编号，业务字段使用 `业务1` 等编号。生成上下文分开登记两类来源；数值匹配不能从工单编号、工序、批次等业务数字借用工艺参数。

| 结论类型 | 必要约束 |
| --- | --- |
| `business_fact` | 单个业务字段的受控完整句子及该业务编号；不可追加作业要求或完成声明 |
| `applicability` | 后端确定的固定选版结论；同时绑定指导书/变更通知及工单编号、型号、工序、实际开工字段 |
| `work_requirement` | 有文档依据的一般作业要求；业务字段和问题中的数字不能豁免文档数值支持检查 |
| `version_comparison` | 仅用于明确的版本比较；分别绑定至少两个业务修订，不得将对比版本用于当前工单要求 |
| `work_order_evidence` | 当前工单事件/检验记录的一句完整原文及工单编号业务来源，不能截掉前置条件、否定或限定 |

业务源包括工单编号、型号、工序、实际开工及精度、报工日期、路线修订、批次、源状态、工厂、登记通知、源修订/系统/记录、源更新时间和采集时间。纯字段问题直接使用业务来源；没有对应文档分块也可回答，未知字段不能从文档推定。

`ManufacturingGenerationDraft` 单独要求结论类型；普通 `EvidenceFirstAnswerDraft` 的生成 schema 保持原样，不扩大旧格式修复预算。两者复用同一生成/修复/校验流程。业务引用格式可以在不调用模型的情况下修复；模型修复及再生成继续使用两类来源和既有共享预算。

制造作业要求额外核对“数量＋件/次”及其引用原文，支持中文和阿拉伯数词的等价表达。文档版本 `2.0` 不能被用来支持错误的“2 件”，首件数量必须由所引指导书正文支持；这一检查不修改冻结评分规则。

生成前校验必需指导书、ECN/通知和检验摘录是否仍完整保留在上下文中。缺少决定性原文或预算不足时拒答，不靠 top-k 缺席来推定不存在通知。显式版本对比可补入同家族、已复核的其他业务修订；这些文档在 API 与页面上标为仅供对比。冲突原文可用于解释拒答，但不开放普通作业内容检索或确定性选版生成。

源连接器还拒绝把服务凭据回显为合法业务字段，避免进入页面、模型或快照。来源字段和文档文字都作为数据处理，不能改变工具、身份、地址或授权。

## 会话、快照与历史授权

制造会话复用原有成对轮次、6 轮追问窗口、租约及 CAS 提交。创建后不能改绑工单；每轮重新读取来源，同工单修订或内容变化也必须重新建会话。普通会话不能临时转换为制造会话，普通会话列表、详情、删除和 `/api/qa` 不能访问制造绑定。

列表、标题、历史、快照、反馈及相关下载按当前全部知识库和业务授权读取。读取自己的业务快照需要当前业务读取权限；非本人共享反馈还需全部相关知识库管理员权限，以及关联源系统/工厂的 `view+review`。知识库管理员不自动成为业务审核者。

制造会话新增永久失效时间。观察到撤权、来源修订/哈希变化或隐私上下文重置后，失效标记独立于后续会话 CAS 成败保存；重新授权不会让该会话继续作为当前事实上下文。授权恢复后可以查看合法的旧历史快照，其来源不会被当前 MES 值覆盖。

成功交付使用一个业务事务保存轮次、回答、文档范围、`QARunBusinessScope`、快照和审计；其中任一环节失败都不留下成功轮次或业务答案。模型调用记账沿用独立事务，回答失败不抹除已经发生的调用。

制造快照为 schema 3，兼容原 schema 1/2 读取，保存白名单工单上下文、选版及审核绑定、SQL 规则摘录、结论绑定、有限核验诊断及用量。原有诊断、候选及 2 MB 总快照上限继续生效；超限标记不完整，SQL 业务访问范围仍保留。关闭 `QA_SNAPSHOTS_ENABLED` 时不创建回答快照，已绑定会话仍按原历史机制私有保存。

M3 尚不批准制造记录成为旧普通回归样本：带 SQL 业务范围的记录在现有样本导出入口明确阻止，等待 M4 的业务证据审核与冻结输入 Worker 接入；不能因快照降级或字段缺失绕过此限制。

## 页面

工作台默认保留“普通知识查询”，新增“工单作业指导”。工单模式提供授权列表分页、只读上下文卡片、适用文件与选版依据、文档和业务字段两种预览。业务预览重新鉴权后查看当次快照；没有快照时核对当前源修订及哈希，不把变化后的值伪装成旧引用。

切换工单、知识库、模式或退出时清空关联状态，以请求代次丢弃旧异步结果。恢复历史标为历史快照；来源冲突、错误、撤权或无法核验时清除旧工单卡片及待展示答案，引导重新查询和新建会话。普通用户补充条件保留在普通模式，不作为 MES 可信字段提交。

## 运行配置与迁移

新增可选 `MANUFACTURING_SOURCES_FILE`，指向 M1 源配置文件，例如 `config/manufacturing_sources.example.json`。文件只引用凭据环境变量；一个租户首期配置一个源系统。未配置时普通功能照常运行，制造接口明确返回源未配置/不可用。不要将示例租户、用户或待审核清单当作新克隆的运行数据库默认值。

迁移 `e9a1c3d54670` 接续 `d8f0b2c43569`，只新增可空 `conversation_business_bindings.invalidated_at`，旧绑定原样保留。存在失效标记时拒绝直接降级。运行该源码的目标数据库需有相应迁移；本轮只迁移随机命名的独立测试库，未修改已有稳定/候选业务数据库或容器。部署与语料配置按 M4 单独记录。

复现检查：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
.venv/Scripts/python.exe -m pytest tests/test_manufacturing_m3_api.py tests/test_manufacturing_m3_conversations.py tests/test_manufacturing_m3_validation.py tests/test_manufacturing_m1_runner.py -q -o 'addopts='
node --test tests/frontend_conversations.test.cjs
.venv/Scripts/python.exe scripts/run_manufacturing_m3_tests.py --create-test-database --output-dir tmp/manufacturing-phase1-m3/新的数据库验证批次
.venv/Scripts/python.exe scripts/manufacturing_phase1_workflow.py check
.venv/Scripts/python.exe scripts/evaluate.py check
```

PostgreSQL 入口只接受本机 `manufacturing_m3_test_<12位十六进制>` 数据库；从空库完整迁移、测试后清理、已有证据不覆盖。前端行为检查执行真实 `app.js` 处理器，使用本地 DOM/HTTP 替身；不是生产部署或真实浏览器视觉验收。实际轮次、成功数量和保留的失败报告见[阶段进度](MANUFACTURING_PHASE1_PROGRESS.md)。这些测试结果不代表 40 题语义准确率、全部权限/故障矩阵或首期发布验收已经完成。
