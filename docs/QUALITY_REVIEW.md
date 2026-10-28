# 质量工作台与回归样本：数据飞轮第二阶段

第二阶段将第一阶段的共享反馈变成可审核、可修订、可导出的内部回归样本。全部操作仍使用 Python/FastAPI、PostgreSQL 和现有知识库权限，不增加模型调用。问题修复、评测任务执行及发布门禁属于第三阶段。

第三阶段已提供[评测与改进](QUALITY_VERIFICATION.md)，可将本阶段的版本接入预算评测、人工复核和问题验证关闭。

## 操作流程

1. 员工提问后提交评价，主动共享给管理员。
2. 如允许数据用于内部回归，再单独勾选“允许审核后的问题、参考答案和必要证据导出”。已有共享反馈迁移后该选项默认为关闭；共享查看不自动授予导出权限。
3. 管理员进入“质量工作台”，或从共享反馈列表点击“审核与标注”。仅拥有原始范围全部知识库 `admin` 权限的账号可以处理记录。
4. 工作台同时展示原问题、实际检索问题、原回答、必要历史、用户反馈和当次证据。
5. 填写问题是否成立、根因和审核说明；标准未齐时保存为样本草稿。
6. 完善可独立理解的回归问题、预期可回答性、参考答案、必答事实和禁止结论。可回答样本还需从快照中选择依据，并填写连续原文摘录。
7. 将样本状态改为“样本审核通过”。审批通过且有导出授权、反馈及语料仍有效时，队列中可勾选该样本。
8. 选择最多100条，填写名称并创建版本。下载 JSONL 和版本清单，保存在本租户受控位置。

## 审核与样本状态

问题状态有 `pending`（待审核）、`confirmed`（成立）、`dismissed`（不成立）、`needs_evidence`（需补证据）。样本状态独立分为 `draft` 与 `approved`。确认问题并不自动批准样本。

根因包括知识缺失、资料过期、解析/切分、召回、重排、上下文、生成、引用、误拒答、问题含糊、权限规则及其他。确认问题必须选择根因，每次保存都必须填写审核说明。

批准样本必须满足：

- 已确认问题，明确预期可回答性，填写参考答案及可独立理解的回归问题。
- 可回答样本有至少一条必答事实和证据。证据编号必须来自当次快照，摘录必须是该片段的连续原文，来源文档仍为相同的就绪版本。
- 资料不足样本填写拒答依据，必答事实和支持答案的证据保持为空，与现有评测加载器的负例约定一致。
- 当次快照状态必须为 `captured`；缺失或不完整观察不能直接作为已批准样本。

事实与禁止结论每组最多32项、每项500字符，证据最多32项、每项摘录2000字符，参考答案最多8000字符。服务校验结构、来源、版本和权限，参考答案的业务正确性由审核者负责，不把字面校验当成独立语义证明。

知识缺失、来源已更新的反馈可以先确认问题并保留草稿。第一版的样本证据只从原快照选取；补充知识后，可用新问答快照建立新的审核记录。旧记录可保留历史证据并转为“需补证据”，不能用旧版本来源直接批准新标准。

## 修订和并发

- 同一租户的一次回答只有一条审核主记录，重复打开返回同一记录。
- 每次保存追加一条修订，记录审核者、时间、完整标准及当时的反馈副本、来源指纹；API不覆盖既有修订。
- 修改须携带整数 `expected_revision`。服务锁定原回答及审核记录，修订号过期返回409，防止多人相互覆盖。界面保留未保存内容，允许重新加载。
- 修订历史按倒序分页；每次最多50条。
- 审核主记录、修订及审计在同一事务中提交，审计失败时回滚。

## 版本与失效规则

每次创建产生新的 UUID 版本ID。名称用于显示，可以重复；清单记录格式版本、样本数量、JSONL SHA-256，以及每个案例的审核修订和来源指纹。JSONL采用固定排序和换行序列化，内容未变化时重复下载字节一致。

数据库只保存版本清单，下载时从仍可访问、仍有效的审核记录重新组装；不额外存放绕过权限的正文副本。

- 用户更新反馈后，旧审核标记为需要复核。
- 用户撤回共享、撤回导出授权、管理员失去任一原范围权限或源文档删除后，后续导出返回404，版本列表不展示相关版本。
- 审核修订、反馈指纹或语料指纹改变后，旧版本返回409，列表显示“已变化，需新建版本”。先重新审核，再创建新版本。
- 语料指纹包含原知识库范围内文档的ID、版本、内容哈希和状态。新增、删除或修改资料都会触发保守失效，即便改变的资料并非当前样本的引用来源。
- 删除会话级联删除回答、反馈、审核及修订；保留的版本清单因成员已不存在而不可再下载，不会生成缺少成员的同名版本。
- 已下载的文件无法远程收回。导出的文件仍须遵循企业内部的数据保留和访问范围。

## 导出格式与现有评测

每行样本兼容 `scripts.evaluate_answers.load_dataset` 的基础契约，包含：

- `id`、经审核的独立 `question`、`reference_answer`、`answerable`。
- `required_facts`、`forbidden_claims` 和 `expected_evidence`（文档名、原文摘录及来源版本）。
- `original_question` 以及 `provenance.observed_retrieval_question`，区分审核后的问题与当时实际检索输入。
- 来源 `run_id`、请求编号、审核者、修订号、反馈/语料指纹。
- `split=regression` 和 `group_key`。同一会话共用组标识；无会话时按租户和规范化的审核问题分组。这只是基础分组，不代替语义去重。

模型原回答不作为金标导出；标准答案来自人工填写。导出不会修改固定120题测试集，不自动生成训练/留出划分，也不声称提高了实际问答准确率。

`source_groups` 使用原知识库UUID字符串，属于追溯和评测配置键，不能授予检索权限。若后续接入现有 live runner，需为该租户单独准备身份和知识库映射：将这些UUID字符串映射到同一UUID，并在评估账号的 `expected_groups` 中配置对应键。仍须通过现有认证与实际权限检查。样本版本导出本身不执行线上评测、不自动调用模型或微调。

## 接口

| 接口 | 行为 |
| --- | --- |
| `GET /api/quality/cases` | 共享反馈审核队列，含尚未建立审核的条目；按知识库、审核状态筛选及游标分页 |
| `POST /api/quality/cases` | `{run_id}`，幂等建立审核，首次201、已有200 |
| `GET /api/quality/cases/{id}` | 当前审核、原快照与历史；`history_before`、`history_limit` 分页 |
| `PUT /api/quality/cases/{id}` | 携带修订号保存人工标准并追加修订 |
| `POST /api/quality/datasets` | `{name, case_ids}`，创建获授权的样本版本 |
| `GET /api/quality/datasets` | 最近可访问的版本及当前有效状态 |
| `GET /api/quality/datasets/{id}/samples` | 重新校验后下载 JSONL |
| `GET /api/quality/datasets/{id}/manifest` | 重新校验后取得版本清单 |

审核请求示例：

```json
{
  "expected_revision": 1,
  "review_status": "confirmed",
  "sample_status": "approved",
  "root_cause": "generation",
  "review_reason": "原文包含例外条件，回答遗漏。",
  "evaluation_question": "哪些条件下可以申请例外处理？",
  "expected_answerable": true,
  "reference_answer": "填写经原文核对的参考答案。",
  "required_facts": ["填写必须回答的条件"],
  "forbidden_claims": ["填写不得出现的错误结论"],
  "evidence": [{"citation_id": "资料1", "quote": "此处必须填写资料1中的连续原文"}]
}
```

反馈接口新增 `allow_dataset_export`，默认 `false`；为 `true` 时必须同时 `share_with_admins=true`。更新反馈会更新来源指纹，已审核记录需重新确认。

## 迁移与验证

迁移 `a5c7e9f10236` 继承第一阶段的 `f4b6d8e0a215`，为反馈增加导出授权，并创建 `quality_cases`、`quality_case_revisions`、`quality_dataset_versions`。

```powershell
.venv\Scripts\python.exe -m alembic upgrade head
```

使用 Compose 的构建、迁移和 API 更新步骤同[第一阶段说明](QA_FEEDBACK.md#启用与迁移)。实施和隔离测试不会自动更新业务 schema 或运行实例。降级会删除审核、修订、版本清单及导出授权字段，应先按需备份。

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.venv\Scripts\python.exe -m pytest -m 'not integration'
node --check backend/app/static/quality.js
node --test tests/frontend_conversations.test.cjs

# 随机隔离 schema，结束后清理；不调用模型。
$env:RUN_QA_FEEDBACK_INTEGRATION_TESTS='1'
.venv\Scripts\python.exe -m pytest tests/test_qa_feedback_postgres.py tests/test_quality_review_postgres.py
```
