# 制造业务证据、冻结输入与质量闭环（M4）

本阶段扩展既有质量工作台、不可变样本版本及预算 Worker。工单源仍是独立只读合成 MES，不是实际工厂接口。源码和隔离检查、真实模型实测、候选部署及首期发布门槛分别记录；最终结果见[实施进度](MANUFACTURING_PHASE1_PROGRESS.md)。

## 审核和来源

原有文档连续摘录继续由 `evidence` 提交；制造快照额外提供只读工单字段供选择，用 `business_evidence` 提交。每项必须匹配当次快照中的 `citation_id`、`field_name`、`value`、源系统、源记录编号、修订及白名单内容哈希，不能把业务编号当文档 UUID。

批准制造样本须有完整 schema 3 快照、SQL 业务范围和三次核验结果。审核者须拥有原范围全部知识库管理员权限及业务 `view+review`。作业样本同时选择工单编号、型号、工序、实际开工，以及决定适用版本的指导书和通知原文。纯业务字段样本可以只有业务证据；澄清、拒答样本仍不填写支持答案的事实或证据。

批准时复读真实来源并核验活跃夹具、当前规则、文档原件与复核。文档摘录仍须逐字属于快照；界面和服务均不允许编辑工单来源值。用户共享和允许回归导出的授权保持独立，合成演示操作明确标为助手非独立复核，不冒充真实员工反馈。

## 活跃夹具登记

服务端设置 `MANUFACTURING_EVALUATION_REGISTRY` 指向[登记样例](../config/manufacturing_evaluation_registry.example.json)。源地址及凭据继续由 `MANUFACTURING_SOURCES_FILE` 和环境变量提供，普通请求不能选择历史版本或任意源。

登记包含每租户唯一的活跃夹具版本及规范化 SHA-256。模拟 MES 新增经过同样源认证的 `GET /v1/fixture`，只返回契约、版本、哈希及身份；不返回题集、答案或所有工单。客户端每次建立检查都重新读取登记并核对源描述，不能仅凭存档文件继续授权。

样本新增：

| 字段 | 绑定 |
| --- | --- |
| `input_kind=manufacturing` | Worker 必须分派制造业务服务 |
| `business_context_binding` | 当次白名单记录、采集元数据、内容哈希和活跃源登记 |
| `applicability_binding` | SQL 制造目录指纹、适用性结果及逐字规则来源 |
| `business_scopes` | 源系统、工厂与工单 |
| `expected_business_evidence` | 人工选择并核验的字段证据 |

语料指纹额外覆盖制造标注与复核。改规则、文档、授权、反馈或登记会令旧样本停止用于新导出或评测，需要重新采集/审核并新建版本。同一源系统和工单的追问在同组，不能分到固定集和反馈集两侧。普通样本的导出和分组保持原行为。

## Worker、评分与验证关闭

`QualityEvaluationWorker` 对制造输入复用 HTTP 的 `ManufacturingService`，三次读取工单并核对冻结记录与适用性；纯字段查询也不能跳过源核验。采集时间在模型输入中固定，最后核验时间仍来自本次读取。每次物理模型调用前另查任务租约、提交者、全部文档和业务权限、样本、目录与活跃夹具。

Worker 不写问答会话或成功 QA 记录，评测记录另保存实际制造观察、回答、上下文、评分和用量，并将其纳入答案复核指纹。失败和撤权阻止后续调用，已经发生的调用保留在独立 PostgreSQL 账本中。SQLite 单元替身只观察调用事件，不伪称具有独立数据库记账；相应行为另在隔离 PostgreSQL 验证。

制造评分位于 `backend/app/evaluation/manufacturing_metrics.py`，组合调用原 `metrics.py`，额外核验来源身份、完整记录哈希、字段值、适用性及业务引用。旧冻结评分文件、题集与历史结果不改写。自动代理仍须逐题语义复核，不把表达等价辅助复核拼成新的准确率。

基线和候选须使用相同样本、语料、业务夹具和评分版本。制造任务还须有可核对的真实代码或配置差异；同配置重复运行不能通过改进检查。结果、人工复核、对比、事件及关闭均重新检查全部关联业务范围。候选必须匹配当前 API 运行指纹，未知用量/未定价仍按原显式确认规则处理，不能把未知费用写成零。

M4 同时将已核验的当前选版结论及两类引用由后端统一编译到可回答作业答复，不依赖模型恰好列全工单字段。显式版本比较保持比较结论，错误模型结论不会被改写成正确答案。工单记录只编译完整、连续的原句，逐句绑定当前工单编号，同步调整覆盖序号；片段、重排和其他字段仍交由原校验拒绝。件数必须由当前所引指导书支持，不能借含有新旧两组数字的 ECN 支持错误数量。日期精度的时分询问直接给出已知日期及精度边界，保留业务字段引用，不补出零点或其他时间。

## 独立部署与初始化

本机新环境使用 8002、独立 PostgreSQL 数据库、Milvus 集合及最小权限账号、Redis、文档卷和 PKCE 客户端。原 8000/8001 实例、数据库、向量集合、账号和浏览器客户端均保留。当前迁移头仍为 M3 的 `e9a1c3d54670`，M4 复用现有 JSON 容器，无新增迁移。

各工具默认只读或要求显式执行。以下占位路径须替换为新的私有目录，不将凭据、令牌或数据库 URL 放在参数和公开文档里：

```powershell
.venv/Scripts/python.exe scripts/prepare_manufacturing_phase1.py --output-dir tmp/新的M4运行目录 --credentials tmp/既有身份凭据.json
# 核对预演后添加 --apply；只接受全新数据库和向量命名空间。
.venv/Scripts/python.exe scripts/configure_manufacturing_phase1_login.py --run-dir tmp/新的M4运行目录
# 登录工具创建本次私有令牌副本，不覆盖旧演示令牌。
.venv/Scripts/python.exe scripts/initialize_manufacturing_phase1.py login --run-dir tmp/新的M4运行目录 --credentials tmp/既有身份凭据.json --apply
```

`Dockerfile.manufacturing-phase1` 在新标签构建，逐项核对实际已安装依赖满足当前精确版本。生成的私有 Compose 启动独立服务；质量 Worker 默认 profile 关闭，只对明确排队的任务执行 `--once`。应用和索引 Worker 使用不同的 Milvus 凭据，权限仅限新集合。

本机复用了已有兼容依赖镜像。新克隆没有该历史标签时，可先用仓库 `Dockerfile` 构建新的依赖镜像标签，再通过 `--build-arg BASE_IMAGE=新的依赖镜像标签` 构建本期镜像；不得为复现而覆盖既有稳定/候选标签。Compose、源配置、身份映射和标注审核仍须按本次独立环境重新初始化。

导入工具只上传校验后的 13 份原件，记录每次响应；出现完整或部分导入记录时不自动重传。资料就绪后 `drafts` 从当前 SQL 子块/父块生成审核计划，保留原始布局字节的摘录及指纹；通知必须保持原文明示的工厂限制。`publish` 要求已核对计划的哈希及复核说明，依次保存草稿、复核同一标注、发布，不自动批准 M0 离线草稿。

## 实测和证据

`scripts/accept_manufacturing_phase1.py` 通过真实认证的同进程 ASGI HTTP 入口调用同一应用及外部依赖，把批次预算传到每次物理模型调用。它不是外部网关压测；本机浏览器显示和交互另验。运行前检查原件及冻结文件、13 份当前发布资料和 SQL 锚点、活跃夹具、身份权限、向量/SQL 数量，并串行记录 20 次合法工单读取。

开发最多 16 题、128 次物理调用、80 万预算 Token、1440 秒；正式一次完整 40 题、320 次调用、200 万预算 Token、3600 秒，未知用量每次预留 32000 Token。每题最多等待 180 秒；超时停止本批，保留最终在途调用用量，不重试。原始响应先落盘，之后才读取快照和评分。

批次中断时保留原目录、原始响应、Trace、快照和账本。只有经对账的开发未执行清单可以用 `--unexecuted-from` 建立新目录，扣除已用调用、Token 和保守时间余量；已执行题不再发送，不将不同代码的两批结果合成独立准确率。正式 40 题不支持拼接或补跑，最终完整批次按开发/回归子集分别报告。

验收产物保存在忽略的 `tmp/`。权限/故障逻辑检查使用 `scripts/manufacturing_acceptance_pytest.py` 记录合成 HTTP 响应、请求号、Trace 及模型输入，明确区别模型替身和真实模型。数据库必需检查从新的随机独立库迁移到实际 head，结束后清理：

```powershell
.venv/Scripts/python.exe scripts/run_manufacturing_m4_tests.py --create-test-database --output-dir tmp/新的M4数据库批次
node --test tests/frontend_conversations.test.cjs tests/frontend/auth.test.mjs
.venv/Scripts/python.exe scripts/manufacturing_phase1_workflow.py check
.venv/Scripts/python.exe scripts/evaluate.py check
```

备份、运行镜像及源码指纹、正式成绩、逐题复核、矩阵覆盖和关闭结果按本轮实际证据登记。回滚优先停止本期实例或切回兼容镜像，保留数据库、文档卷、向量和原批次；不默认执行数据库降级或删除卷。

2026 年 11 月 1 日实际正式结果为自动 28/40、助手非独立复核 29/40，**未通过发布门槛**；真实闭环保持待改进，详见[M4 实测与交付](MANUFACTURING_M4_ACCEPTANCE.md)。已执行付费范围不能自动重跑。离线报告入口不会调用模型，例如：

```powershell
.venv/Scripts/python.exe scripts/report_manufacturing_phase1.py --logic-evidence tmp/本轮逻辑/cases tmp/补充权限/cases --frontend-junit tmp/前端.xml --formal-dir tmp/正式40题 --reviews tmp/逐题复核.jsonl --output-dir tmp/新的离线汇总
```

复核 JSONL 每题须包含 `case_id`、实际 `response_binding`、`reviewer`、`verdict` 和 `reason`；自动分与复核分分别报告，助手复核明确标为非独立。矩阵脚本按测试标识建立证据索引；非 HTTP 仓储检查、DOM 替身和真实浏览器观察分别说明，不伪造统一传输层。
