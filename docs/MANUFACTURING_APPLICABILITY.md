# 制造适用性目录与选版（M2）

M2 提供可审核的制造文件目录、确定性选版和检索范围。入口为 `backend/app/manufacturing/metadata.py`、`applicability.py`、`scope.py`。制造用户端问答、MES 事实复核、会话绑定、两类引用和页面已由 [M3](MANUFACTURING_QA.md) 接入；普通 `/api/qa` 不会自动查询 MES。

本阶段未向既有演示库导入标注、批准规则、迁移数据、重建索引或更新容器。M0 清单中的五条规则仍是离线草稿；不能将题集、标准答案或评测脚本作为运行期规则来源。

## 审核流程

所有接口沿用 Keycloak 身份及知识库 `editor` 权限，只能操作当前租户的当前文档；响应禁止缓存。

| 操作 | 接口 | 关键约束 |
| --- | --- | --- |
| 预览权威正文与锚点 | `GET /api/knowledge-bases/{kb_id}/documents/{doc_id}/review` | 可用 `expected_version`、`after_index`、`limit` 分页；`include_parents=true` 返回当前父块 |
| 查看制造标注 | `GET /api/knowledge-bases/{kb_id}/documents/{doc_id}/manufacturing-metadata` | 未配置时 `manufacturing=null`、`revision=0` |
| 保存草稿、复核草稿 | `PUT /api/knowledge-bases/{kb_id}/documents/{doc_id}/manufacturing-metadata` | 文档版本和标注修订双重并发检查 |
| 发布或撤回文档 | `PUT /api/knowledge-bases/{kb_id}/documents/{doc_id}/publication` | 复用已有发布接口和发布修订；制造资料必须有当前适用性复核 |

操作顺序：

1. 编辑者预览当前文档及 SQL 分块。预览返回分块 `id`、`content`、`content_hash` 和 `vector_generation`；父块窗口额外返回父块 ID、正文、指纹及索引区间。不能从过期向量记录或本地另存的文本构造锚点。
2. 写入 `state=draft`、当前 `expected_document_version`、当前 `expected_revision`、完整 `metadata` 和非空 `review_note`。每次保存递增标注修订，并使文档发布状态回到草稿。
3. 核对实际原件、业务版本、适用范围、摘录和条件后，使用同一份完整标注、最新修订写入 `state=reviewed`。初次直接复核、复核时同时改变标注、使用旧文档版本或旧修订都返回 409。
4. 使用最新的发布修订调用既有发布接口。索引必须完成且有正文；OCR 资料继续要求明确确认识别限制。发布时再次检查制造审核及 SQL 锚点。

制造元数据写入使用与索引相同的“租户行 → 文档行”锁顺序。标注、发布失效及审计在同一事务提交；审计失败整体回滚。审计记录操作者、文档版本、制造修订、状态和标注摘要指纹，不保存正文或凭据。当前完整复核记录位于文档的 `metadata_json.manufacturing`。

## 标注结构

`PUT` 请求顶层字段：

| 字段 | 含义 |
| --- | --- |
| `expected_document_version` | 当前技术上传版本，正整数 |
| `expected_revision` | 当前制造标注修订；初次为 0 |
| `state` | `draft` 或 `reviewed` |
| `metadata` | 下述 `manufacturing-metadata-v1` 标注 |
| `review_note` | 1～2,000 字符的复核或修改说明 |

`metadata` 字段：

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 固定为 `manufacturing-metadata-v1` |
| `document_family_id`、`document_code`、`business_revision` | 文件家族、编号和业务版本，独立于技术版本与 MES 修订 |
| `source_kind` | `formal_document`、`written_notice`、`historical_record` |
| `document_role` | `instruction`、`inspection`、`change_notice`、`event_record`、`inspection_record`、`reference`、`route` |
| `scope_kind` | `company`、`product_operation`、`work_order` |
| `product_models`、`operation_codes` | 非公司范围必须明确产品和工序；使用来源中的精确值 |
| `plant_codes`、`route_revisions` | 来源明确限定时填写；未限定为空数组，不猜测路线 |
| `work_order_ids` | 工单范围必须列出具体工单；事件、检验记录必须采用工单范围 |
| `effective_from`、`effective_to` | 明示的日期区间，允许未知；起止不能倒置 |
| `archived` | 归档说明；不能替代发布撤回，也不会抹除有规则支持的旧版适用性 |
| `notice_state` | 通知 `active` 或 `cancelled`；取消通知不产生选版授权 |
| `business_timezone` | 明确业务时区，默认 `Asia/Shanghai`，必须是有效 IANA 时区 |
| `source_anchors` | 1～8 个 SQL 原文锚点，证明文件身份与适用范围 |
| `rules` | 最多 16 条显式选版规则；只允许来自变更通知，历史事件不能充当书面授权 |

每个原文锚点包括：

- `chunk_id`、`vector_generation`、`chunk_content_hash`：当前 SQL 子块身份。
- `quote`、`quote_sha256`：逐字摘录及 UTF-8 SHA-256；不做空格、换行或标点归一化。
- 可选的 `parent_id`、`parent_content_hash`：摘录跨子块时使用当前父块窗口，二者必须同时填写。

服务端核对租户、知识库、文档、索引代次、技术版本、原件指纹、分块正文指纹及摘录。父块还需覆盖当前子块索引。一个子块关联多条摘录时，它们必须能在同一个已核验窗口中完整展示。找不到 SQL 原文、同 ID 下正文变更、错误哈希及过期代次都不能保存或通过复核。

每条规则都有 `rule_id`、目标文件编号 `target_document_code`、目标业务修订 `target_business_revision`、精确产品/工序和自身的 `anchor`。

| `kind` | 条件 |
| --- | --- |
| `start_before` | 必填 `cutoff_date`，实际开工日期严格早于界限 |
| `start_on_or_after` | 必填 `cutoff_date`，实际开工日期等于或晚于界限 |
| `work_order_override` | 必填 `work_order_ids`、`effective_from`；可列 `supersedes_rule_ids` 表达原文明示的替代关系 |

开工规则不接受工单例外或替代关系；工单通知不能扩大其文档的明示工单范围。空白、额外字段、错误类型、重复规则 ID、自我替代及缺少来源摘录均不能作为有效规则。

## 选版与时间依据

服务入口为 `ApplicabilityResolver.resolve(context=..., scope=..., business_scope=..., session=...)`。`context` 必须来自 M1 经过鉴权的工单读取服务；`scope` 是当前知识库授权生成的基础范围；业务范围还需匹配租户、源系统和工厂。M3 必须在业务调用及生成前后复查当前权限和源修订。

选版读取当前授权目录，不依赖向量或关键词结果：

1. 只接受索引完成、当前发布、当前技术版本且标注复核有效的资料，逐字核验来源锚点。已发布但复核异常的资料要求处理缺口。
2. 按产品、工序、必要工厂、路线和具体工单定位文件。来源限定了路线而工单路线未知时返回缺条件，不能默认匹配。
3. 开工切换只使用 `actual_started_at`。日期精度保持原样；带时区时间戳转换到已审核业务时区后取日期。报工日期、文件上传日期和问题日期不能代替实际开工日期。
4. 工单通知需对象匹配、状态有效并已生效；生效时点使用可信上下文的 `last_verified_at` 在业务时区的日期。通知的明确起止区间继续有效。具体书面工单例外优先于一般开工窗口。
5. 多条有效通知指向不兼容版本时返回冲突；只有原文明示、可核验且无环的替代关系可以消解。不能按文件名、上传日期、技术版本或业务版本数字选“最新”。
6. 所选指导书可以是归档旧版，其名义有效期不能先于在制沿用规则将它剔除。适用检验规范、公共参考及记录使用已复核的范围和明确区间；重叠的同编号规范不能混用。
7. MES 指向的通知在当前可见、已复核的资料中不可取得时返回缺口。没有可见通知不等于所有系统均不存在通知。

| 结果 | 后续取证行为 |
| --- | --- |
| `applicable` | 返回选定文件、业务版本、规则 ID、带制造约束的 `RetrievalScope` |
| `needs_context` | 明确缺字段、待处理复核或处理上限；正文范围为空 |
| `no_evidence` | 无匹配指导书或无已复核的选版依据；正文范围为空 |
| `conflict` | 无法消解的规则或文件身份冲突；正文范围为空，可保留不可检索的冲突依据解释拒答 |

M3 仅在明确版本对比问题中启用后端 `include_other_revisions`，补入同家族已复核的其他业务修订，标记为 `comparison_only`；不得将其作为当前工单作业依据。冲突依据单独保存为解释依赖，也参与目录及来源刷新检查，不开放普通作业检索。

首期工作量上限为：一个就绪且已发布的授权目录 256 份制造资料、一次判断 64 条相关有效规则、32 份适用文件、24 个必需原文锚点、32 个证据分块。达到上限要求缩小范围或复核，不静默丢弃决定性依据。

## 检索与刷新约束

返回的 `decision.scope` 携带不可变 `ManufacturingFence`，只由后端传递。调用端必须将它贯穿关键词、向量、重排、Agent search/read、邻块、父块、正文补取、反馈搜索及生成前后刷新；不能在中途重新构造一个只有知识库 ID 的普通范围。

- SQL 候选在 LIMIT 前校验文档白名单、技术版本、原件哈希、制造审核修订和摘要指纹；Milvus 在 ANN top-k 前增加文档 ID 白名单。
- 必需指导书锚点、决定选版的 ECN/通知和检验依据直接从 SQL 获取，标记为 `manufacturing_required`，不参加 top-k 竞争；Agent 丢失它们时刷新会重新补齐。
- 制造模式不再按问题日期套用普通时间过滤，因此能保留“旧版指导书＋后来发布的在制沿用 ECN”。普通问答仍使用原有时间处理。
- 每次取证复查本轮工单相关的已发布制造目录指纹与必需来源。相关资料新增、审核变化、撤回、换版、原文或锚点变化都会抛出 `ManufacturingScopeChanged`，要求重新选版；不能只删掉决定性通知后继续使用旧指导书。
- 该异常穿透 Agent 和反馈补查，不降级复用旧答案。M3 的制造 HTTP 入口将它转换为可理解的重新查询提示。

Trace 分开记录 `manufacturing_selection` 和 `manufacturing_rule_evidence`，之后再记录普通向量、关键词、融合、重排及补查。Trace 不赋予访问权限。

2026-11-06 起，目录依赖按后端已核验工单中的产品、工序、工厂、路线及具体工单范围建立。未发布资料不参与选版和当前目录指纹；复核有效且明确与本轮无关的资料编辑不会中断回答。路线未知时仍保留路线限定资料并要求补条件；已发布但复核异常的资料不能凭其不可信范围标注被排除。MES 登记的通知也继续纳入检查。

检查每次重新查询授权目录，不限于初次引用的文档 ID，因此新增适用通知、草稿转为相关发布资料、其他资料范围变为适用、相关来源被撤回或移出范围仍会中断。该优化不放宽 SQL/Milvus 的检索白名单、原文锚点、当前授权或三次工单源核验。历史回答和冻结版本保留原指纹，不自动迁移或重新评分。

## 验证与阶段边界

定向测试：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
.venv/Scripts/python.exe -m pytest tests/test_manufacturing_metadata.py tests/test_manufacturing_applicability.py tests/test_manufacturing_retrieval.py tests/test_manufacturing_m1_runner.py tests/test_evidence_coordinator.py tests/test_evidence_feedback.py -q -o 'addopts='
.venv/Scripts/python.exe scripts/run_manufacturing_m2_tests.py --create-test-database --output-dir tmp/manufacturing-phase1-m2/新的数据库验证批次
.venv/Scripts/python.exe scripts/manufacturing_phase1_workflow.py check
.venv/Scripts/python.exe scripts/evaluate.py check
```

PostgreSQL 入口复用 M1 的独立库保护和脱敏记录逻辑，但严格要求 `manufacturing_m2_test_<12位十六进制>` 名称，不能复用 M1 或业务库。显式创建时从空库迁移，验证完成清理；已有报告不能覆盖。M2 使用现有 JSON 容器，不新增迁移；M2 当轮验证 head 为 `d8f0b2c43569`，当前入口随源码验证最新迁移 head，M3 新增迁移见[制造问答说明](MANUFACTURING_QA.md)。

单元测试用本地 SQL 原文和假向量/模型验证逻辑；PostgreSQL 测试验证真实 JSON 过滤、pg_search 查询、行锁并发与跨会话失效。具体轮次及结果见[阶段进度](MANUFACTURING_PHASE1_PROGRESS.md)。这些结果不代表 40 题正式问答质量或整期验收；付费问答、业务样本回归和部署留待后续已定义阶段。
