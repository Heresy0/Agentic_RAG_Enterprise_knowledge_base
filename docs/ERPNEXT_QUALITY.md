# ERPNext 质量审核与冻结验证（E4）

本阶段把自建 ERPNext 的工单、所选工序和作业卡纳入现有审核、不可变样本版本及质量 Worker。平台与 ERP 连接继续只读；审核、反馈、版本和验证结果只写平台数据库，不向 ERP 写入。自建演示记录不视为真实工厂证据，助手审核应标注“非独立验收”。

## 实时来源登记

ERPNext 不提供模拟 MES 的 `/v1/fixture`。适配服务新增受相同租户、工厂及源令牌检查的 `GET /v1/source`，只返回 `erpnext-live-source-v1` 身份、投影契约及配置哈希，不返回源地址、凭据或所有工单。

服务端 `MANUFACTURING_EVALUATION_REGISTRY` 可配置 [v2 登记](../config/erpnext_evaluation_registry.example.json)。`source_kind=live`、`source_contract=erpnext-read-only-v1:execution-v1`，`source_sha256` 必须来自已鉴权实时描述的 `configuration_sha256`；样例的全零哈希须替换，不能当作有效登记。登记绑定公司到工厂映射、站点时区、ERP 地址哈希及投影契约，普通请求不能指定源地址或登记。原 v1 夹具登记、旧冻结契约与评分继续兼容。

实时来源的审核批准、导出、任务准备、调用前检查及结果访问都会核验登记和对应记录。仅来源描述一致不足以通过：必须复读被冻结的工单、工序及作业卡，并核对修订与完整白名单哈希。来源改变或不可用会停止使用旧样本，不能从存档中继续授权。无关卡在父工单未变化时不会使所选卡样本失效。

## 审核与冻结范围

审核者需要原范围全部知识库管理员权限及业务 `view+review`。用户主动共享反馈和允许导出分别检查；撤权、取消共享或导出授权会立即限制访问和后续使用，不自动给整个部门提升权限。

业务证据保留源实体、实际记录编号、工序/卡编号、字段值、时间精度、源修订与内容哈希。页面只允许选择快照字段，不能编辑其值或把卡来源换成工单来源；篡改或省略所选卡身份会被拒绝。可回答的工序/卡样本必须选取对应身份字段。作业指导样本仍须有工单编号、型号、工序、工单实际开工及决定选版的指导书/通知原文；纯字段样本可以只有业务证据。

冻结记录使用 `work-order-execution-v1`，完整保存三层实际时间及所选身份。工单实际开工仍是选版依据，未知工序/卡时间不以父级或计划时间补齐。审核新版本、来源变更或重新采集均不覆盖旧版本。同一来源、同一工单下的不同工序/作业卡属于同组，不能分别进入固定集和反馈集，防止泄漏。

Worker 复用制造问答服务，把冻结的工序及卡编号原样传入三次来源校验，固定采集时间，保留本次最后核验时间。Worker 不新增问答/会话成功记录。执行上下文使用 `manufacturing-execution-v1` 评分，额外验证完整选择、实际字段及各实体引用；选择编号可通过完整上下文和字段引用中的身份确认，开工字段仍须有对应引用。旧工单评分与冻结题集不改写。

## 零调用验证与模型验证

工作台“评测与改进”默认选择“来源与确定性字段验证（0 次模型调用）”。请求须同时使用 `allow_model_calls=false`、`max_model_calls=0`、`max_tokens=0`，调用上限和 Token 上限在 Worker 内执行；需要生成、改写、嵌入或重排的问题会停止，记录失败，不自动提高额度或补跑。简单的工序/作业卡开工时间可由现有后端投影验证完整冻结链路。

模型验证仍须明确选择允许模型、确认预算并提供正调用额度及有效 Token 预留。任务创建不等于本会话批准任何付费运行；本阶段验收只使用零调用任务。固定集与反馈集仍须是两份不同版本且无同工单重叠，原任务租约、取消、授权及独立用量账本继续生效。

候选不常驻运行质量 Worker。页面创建零调用任务后，用当前候选配置单次启动：

```powershell
docker compose -f tmp/erpnext-quality-20261109-r1/compose.json run --rm --no-deps api python scripts/run_quality_worker.py --once --zero-model-calls
```

该入口只领取未允许模型且调用/Token 预算均为零的排队任务，并在进程内再次强制零预算；不会领取允许模型的任务。`--allow-model-calls` 与 `--zero-model-calls` 互斥，常规模型 Worker 的明确授权要求保留。

源码测试入口：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_erpnext_quality.py tests/test_manufacturing_m4.py tests/test_quality_review.py tests/test_quality_verification.py
.venv/Scripts/python.exe scripts/run_manufacturing_m4_tests.py --create-test-database --output-dir tmp/新的独立质量验证
node --test tests/frontend_conversations.test.cjs tests/frontend/auth.test.mjs
```

独立 8003 的实际部署、真实 ERP API 验证、零调用账本及备份证据在本轮交付后单独记录；这些功能验收不构成模型问答质量通过，历史开发 13/16、正式 31/40 的失败仍保留。
