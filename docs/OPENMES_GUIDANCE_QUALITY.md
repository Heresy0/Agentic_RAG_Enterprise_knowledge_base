# OpenMES 步骤指导书映射与质量冻结

本阶段复用制造文档适用性审核、业务来源校验和质量 Worker，连接 OpenMES 的具体步骤与指导书，并将工单、批次、步骤纳入反馈审核及不可变样本。平台审核写入本地数据库，OpenMES 仍只有 GET 读取；ERPNext 环境独立保留。

## 审核映射

映射采用**具体步骤明确分配版本**：范围为源系统、工厂、工单、批次、步骤和知识库，审核者选择已发布、已完成制造适用性复核的指导书及业务版本，明确工艺编码与审核说明。系统校验文档的产品、工厂、路线、工单和工艺范围及原文锚点；不从步骤名称识别工序，不将步骤或批次时间补为工单实际开工，也不据此套用工单开工窗口。平台映射和 OpenMES 原始字段分别展示，不能把平台审核编码称为 MES 工单源字段。

这是经授权的步骤版本分配，审核者须核对在制沿用及相关通知；已有明确工单例外与分配版本冲突时拒绝。相关已发布通知仍进入来源目录和回答证据，并参与新增、撤回与变化检查。本阶段不做模板级自动批量映射，也不自动把“最新”指导书分配给所有步骤。

审核需要该知识库 `admin` 及对应工厂业务 `view+review`。先保存草稿，再以相同来源记录与指导书版本复核；仅最新 `reviewed` 修订生效。新草稿和撤回停止使用旧分配，各次状态与理由追加为新记录，旧历史不覆盖。来源、文档版本、制造复核或发布状态变化后，须重新采集并审核。一个查询范围内多个知识库给同一步骤不同分配时返回冲突，要求明确范围。

工作台选择单一管理知识库及具体步骤后，在“步骤指导书审核映射”加载当前版本、候选指导书及修订历史。选择指导书、填写依据，依次保存草稿和复核启用；也可撤回。保存后开始新会话。历史会话不提供映射修改入口，来源或权限错误清除旧审核表单，晚到响应不能恢复旧范围数据。

接口（均使用现有认证）：

```text
GET /api/manufacturing/work-orders/{wo}/batches/{batch}/steps/{step}/guidance-mapping?knowledge_base_id=...
GET /api/manufacturing/work-orders/{wo}/batches/{batch}/steps/{step}/guidance-mapping/candidates?knowledge_base_id=...
PUT /api/manufacturing/work-orders/{wo}/batches/{batch}/steps/{step}/guidance-mapping?knowledge_base_id=...
```

PUT 使用 `expected_revision`、`state=draft|reviewed|withdrawn`、`review_note`；草稿/复核另需 `document_id`、`operation_code`、`expected_source_revision`。正文、业务版本、源字段值及原文锚点由后端复读，不接受客户端覆盖。GET 历史可用 `before_revision`，每页最多50条。审核保留来源前后复读及最终权限检查，知识库范围和来源由服务器配置决定。

制造问答保留三次完整来源与权限校验，额外复查本轮步骤的映射修订。回答的 `applicability.step_guidance` 记录分配修订、指导书及业务版本、审核工艺编码、理由和审核者，文档原文仍使用既有引用。会话首次提问绑定步骤映射摘要；映射变更后旧会话在追问模型调用前返回409。其他步骤的独立映射变化不影响本轮。

迁移 `e9b2d4f60713` 增加追加式 `step_guidance_revisions` 和可空会话映射摘要，仅部署到 OpenMES 候选库。已有审核历史或映射绑定会话时拒绝降级丢弃数据；原 ERPNext 库和容器不随源码迁移。

## 批次与步骤质量审核

实时来源使用鉴权 `/v1/source`、`openmes-live-source-v1` 和 `openmes-read-only-v1:qa-v1`。服务端 [v2登记样例](../config/openmes_evaluation_registry.example.json)须填入真实描述的配置摘要，全零示例不能通过核验。审核批准、导出、Worker和结果访问都复读对应选择的完整来源，不能以归档响应或来源描述单独授权。

业务证据选择保留 Work Order / Batch / Batch Step、真实记录编号、批次及步骤编号、字段值、时间精度、源修订和哈希。客户端只能选快照中的字段，不能改值或实体；可回答样本须选工单及所选批次、步骤身份。批次样本不伪造步骤，未知时间保持未知。作业指导样本还须有有效步骤映射、型号及决定版本的指导书/通知原文；没有映射的字段样本不能直接批准为作业指导样本。

反馈共享、导出同意、知识库管理员和业务 `view+review` 继续分别校验。同一来源、同一工单下的不同批次/步骤属于同组，不能拆入固定集和反馈集。冻结版本保存原完整上下文、映射和适用性；来源或映射改变后旧版本失效，不覆盖旧版本或恢复旧修订。

Worker 原样传回批次、步骤编号，保持三次校验；新增 `manufacturing-openmes-v1` 评分核对完整上下文和逐实体引用，旧工单及 ERPNext 评分口径不变。默认零模型任务与 `--once --zero-model-calls` 继续执行硬零预算，需要模型时停止，不自动改额度。真实模型验证需按现有显式模型模式及预算执行，本阶段工程验收不产生模型准确率。

## 验证入口

```powershell
.venv/Scripts/python.exe -m pytest tests/test_openmes_guidance.py tests/test_openmes_quality.py tests/test_openmes_qa.py tests/test_erpnext_quality.py
.venv/Scripts/python.exe scripts/run_manufacturing_m3_tests.py --create-test-database --output-dir tmp/新的步骤审核数据库验证
.venv/Scripts/python.exe scripts/run_manufacturing_m4_tests.py --create-test-database --output-dir tmp/新的质量数据库验证
node --test tests/frontend_conversations.test.cjs tests/frontend/auth.test.mjs
```

实际部署、失败记录及本轮验证结果在收尾报告中单独记录。自建 OpenMES 演示记录不是真实工厂证据，工程替身和零模型验证不视为真实模型或独立业务验收。

## 2026-11-10 工程验收

业务提交 `609d23d2095cc1324192a67be08c33f29dcf745b` 已从Git归档部署到 [8004工作台](http://127.0.0.1:8004/) 及 OpenMES适配器，最终镜像 `enterprise-knowledge-manufacturing:openmes-guidance-quality-20261110-r2`，ID `sha256:035737a9a4e4412ec1e28e281863139fe2bc2035ebeaaba62faa9e6eac801a61`。两者306个部署文件（含依赖清单及alembic.ini、后端167个Python）与归档一致，提交标签一致；仅OpenMES库迁移到 `e9b2d4f60713`。ERPNext库仍为 `f2b8c0a961d3`，8个原容器ID/镜像/启动时间和17份旧报告哈希不变。

完整非集成 **2352通过、1既有跳过、98集成排除**，前端 **65通过**，独立PostgreSQL M1/M3/M4 **9/14/12通过**并移除测试库。自动测试覆盖指导书回答、作业样本审核与映射变更失效、工单例外冲突、历史与会话、来源/文档/权限变化及批次/步骤冻结；生成使用工程替身。原120题及首期40题契约完整性通过，没有运行模型题。

真实来源验收 **71个请求**（含快照读取），包含5次映射修订、5个审核样本和5份冻结版本；7个成功问答快照均有三次来源校验。先后两次使用正式 `--once --zero-model-calls` CLI完成各2题任务，分别包含真实批次和步骤来源；场景有重叠，不计模型准确率。故障验证证明：映射撤回阻止旧会话并使相关冻结版本失效；步骤源变化返回409，恢复字段值不恢复旧修订；撤销review阻止审核/导出而普通view查询仍可用；登记变化和适配器停机中止使用旧证据。最后恢复映射、源值、权限、登记和适配器，并重新采集冻结两份当前版本完成第二次任务，无活动质量任务。

同进程问答及Worker均有硬零预算，25个可观察请求ID、两个任务及整个候选独立模型账本新增调用和Token **0**。r2最终部署端口另外5项、上游只读4项、新旧健康端口4项及页面资源字节4项通过。仅给现有知识管理员增加新候选 `openmes-hc / HC-P01 / review`，未提升知识库、部门、上游或ERPNext权限。

首轮旧测试仍要求缺工单开工时间，按新映射契约修正后通过；原记录保留。r1镜像应用文件正确但继承了依赖镜像的旧提交标签，保留r1后构建r2显式标注本轮提交；业务代码、数据库和模型用量没有二次变化。迁移前后平台/MES数据库共4份备份目录核验通过，未做完整恢复演练；未重建索引、常驻新Worker或做浏览器视觉检查。

私有配置、备份、原始响应及Trace在 `tmp/openmes-guidance-quality-20261110-r1/`，凭据不提交。当前检查和零模型任务入口：

```powershell
docker compose -f tmp/openmes-guidance-quality-20261110-r1/compose.json ps
docker compose --env-file tmp/openmes-phase1-20261109-r1/deployment/deployment.env -f deploy/openmes/compose.yaml -f tmp/openmes-guidance-quality-20261110-r1/adapter.compose.json ps
# 仅在页面创建零调用任务后执行；不会领取允许模型的任务。
docker compose -f tmp/openmes-guidance-quality-20261110-r1/compose.json run --rm --no-deps api python scripts/run_quality_worker.py --once --zero-model-calls
```

完整结构化结果见[本轮报告](reports/openmes-guidance-quality-20261110-r1.json)。这是工程接入通过，真实模型问答质量仍待独立验证，旧ERPNext10/16、模拟开发13/16及正式31/40失败状态不变。
