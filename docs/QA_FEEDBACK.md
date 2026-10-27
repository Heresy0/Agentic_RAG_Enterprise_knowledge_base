# 问答快照与用户反馈：数据飞轮第一阶段

本阶段把线上回答和反馈关联起来，支持私有问答快照、点赞/点踩、反馈列表和管理员查看用户主动共享的数据。

当前第二阶段已实现审核、修订历史和内部回归导出，详见[质量工作台说明](QUALITY_REVIEW.md)。微调及自动评测发布尚未实施。

## 使用方法

1. 执行数据库迁移，再更新 API 进程或容器。
2. 正常提问；回答下方出现“赞 · 有帮助”“踩 · 待改进”和“查看问答快照”。
3. 选择评价，填写说明；点踩必须选择问题原因。
4. 默认只有本人能看到反馈。勾选“提交给相关知识库管理员”后，允许符合权限的管理员查看本次问题、答案、必要历史和证据。
5. 左侧“问答反馈”查看自己的评价，按当前知识库和评价筛选。管理员可切换到共享反馈；列表支持游标分页。
6. 重复评价更新原记录；取消共享立即收回管理员的后续访问。本人可以撤回评价。已加载到浏览器的数据无法远程收回。

旧会话中的历史回答不会补造快照。更新后生成的会话轮次可在恢复历史时重新关联 `run_id`。

## 启用与迁移

`QA_SNAPSHOTS_ENABLED=true` 为默认值；设为 `false` 停止采集新快照，已有记录仍按当前权限访问。浏览器无需额外配置。

本地 Python 运行：

```powershell
.venv\Scripts\python.exe -m alembic upgrade head
```

使用现有 Compose 部署时，先构建新镜像，再使用新镜像运行迁移，最后更新 API。以下操作需要在决定更新本地实例时执行；代码实施及隔离测试不会自动更新业务数据库或正在运行的实例：

```powershell
docker compose build api
docker compose run --rm --no-deps api python -m alembic upgrade head
docker compose up -d --no-deps api
```

迁移 `f4b6d8e0a215` 继承当前 `e1a3c5f7b902`，新增 `qa_runs`、`qa_run_scopes` 和 `qa_feedback`。降级会删除本阶段采集的数据，应先按需要备份。

## 快照内容与边界

- `run_id` 是请求编号对应的 UUID；关联租户、本人、可选会话与轮次。
- 保存原问题、实际检索问题、最终公开答案及引用、当次最终上下文原文和渲染文本。
- 最多保存最近六轮实际用于追问改写的历史，答案按改写服务相同的 1200 字符限制保存；记录历史引用的文档 ID，避免删除历史来源后从快照恢复其内容。
- 记录文档 ID、版本、索引 generation、父子块 ID 和证据内容 SHA-256；来源变化后仍能核对当时内容。
- 保存主要模型及策略配置、应用 Python 源码和提示词指纹。指纹在进程内缓存，发布代码后应重启进程。该记录用于追溯，不承诺外部模型可逐字复现。
- 保存模型调用用量的白名单字段，未知 Token/费用保留 `null`；使用相同 `request_id` 关联现有独立用量账本。
- 不采集访问令牌、配置密钥、原始模型隐藏推理和内部存储 URI。文档本身的正文按当前用户权限保存；普通日志和审计不保存这些正文。
- 单份上下文最多 100 项、100000 字符，序列化快照上限 2 MB。超限或发现来源范围不一致时标为不完整，保留安全的部分且不保留原始渲染文本。

状态：`captured` 表示上下文已采集，包含无检索证据的空上下文；`not_observed` 表示链路没有提交上下文观察；`incomplete` 表示超限或上下文存在缺口。诊断落库失败时回答返回 `snapshot_status=unavailable`，不提供反馈链接，日志仅记录请求编号和异常类型，并增加 `enterprise_qa_snapshots_total{outcome="unavailable"}`。

快照在事务保存点中写入，与成功问答的审计/会话提交一起完成。诊断写入失败回滚保存点，正常问答及会话仍可完成；业务事务本身失败仍按原有接口处理。当前只对成功返回的问答（包括正常拒答）生成记录，鉴权失败、限流和异常请求继续由现有审计、日志和用量账本记录。

## 权限与删除

- 本人只能评价自己的回答；反馈与租户、回答所有者在数据库中通过复合外键绑定。
- 所有读取和写入重新检查当前用户状态、租户和知识库权限，查询统一先做租户过滤。
- 原始检索范围全部保留；跨库快照要求本人仍可访问原范围的所有库。管理员必须拥有原范围全部知识库的 `admin` 权限，且用户当前仍选择共享。只管理某个被引用的库不足以查看整次回答。
- 源文档或必要历史引用的文档已被删除时，不展示该快照及相关反馈正文。知识库被删除或禁用也不会扩大可见性。
- 删除会话级联清理其问答记录、范围记录和反馈；撤回反馈保留本人快照，但收回共享访问。
- 本阶段未实现定时到期清理或审核权限委派；第二阶段的数据集导出另需本人允许。单次问答记录按现有备份和数据库维护策略管理。

## API

| 接口 | 行为 |
| --- | --- |
| `POST /api/qa` | 原接口新增可选 `run_id`、`snapshot_status`；关闭采集时不增加这两个字段 |
| `GET /api/qa/runs/{run_id}` | 查看本人或经共享授权的快照及当前评价 |
| `PUT /api/qa/runs/{run_id}/feedback` | 本人创建/更新评价，同一回答最多一条 |
| `DELETE /api/qa/runs/{run_id}/feedback` | 本人幂等撤回评价 |
| `GET /api/qa/feedback` | `view=mine/shared`，可选 `knowledge_base_id`、`rating`、`reason`、`limit` |

评价请求：

```json
{
  "rating": "down",
  "reason": "incomplete",
  "comment": "遗漏制度中的例外条件",
  "share_with_admins": false
}
```

`rating` 为 `up/down`；点踩原因限定为 `incorrect/incomplete/citation/false_refusal/outdated/other`。点赞必须将 `reason` 设为 `null` 或省略。说明最多 2000 字符，未知字段拒绝接受。

第二阶段增加 `allow_dataset_export`，默认 `false`，仅在 `share_with_admins=true` 时允许设为 `true`。旧反馈和旧客户端不会自动获得导出授权。

列表返回 `next_cursor`，下一页同时提交 `before`（带时区）与 `before_id`。列表按更新时间、ID 降序；反馈更新后可能移动到较新位置，客户端刷新时重新从首页读取。因来源删除而隐藏记录时，某页可能不足 `limit`，仍应按返回游标继续读取。

## 验证

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.venv\Scripts\python.exe -m pytest -m 'not integration'
node --check backend/app/static/app.js
node --test tests/frontend_conversations.test.cjs

# 需要已有 PostgreSQL；仅创建、操作并清理 qa_feedback_test_<随机ID> 隔离 schema。
$env:RUN_QA_FEEDBACK_INTEGRATION_TESTS='1'
.venv\Scripts\python.exe -m pytest tests/test_qa_feedback_postgres.py
```

接口和页面测试使用模拟模型，覆盖快照冻结、权限撤销、跨库管理员授权、共享撤回、反馈审计事务、保存点回滚、分页、历史恢复、前端文本渲染以及过期异步响应丢弃。PostgreSQL 测试覆盖真实迁移升降级、并发评价唯一性、复合外键和删除级联，不调用模型。
