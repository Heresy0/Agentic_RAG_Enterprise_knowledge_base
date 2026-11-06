# 新会话工作入口

先阅读本文件，再按任务读取对应源码和文档；不需要通读完整 README、所有历史报告或 tmp。

## 项目范围

- 多租户企业知识库问答系统，当前以制造业资料进行演示和评测。
- 普通解析和 OCR 已接入审核发布、权限问答、原件追溯、反馈审核及回归验证闭环。
- ERP/MES 实时接口尚未接入；视觉解析尚未完成接入实测。历史实施方案中的这些内容属于后续设计。
- 首期工单集成在 `codex/manufacturing-phase1` 分阶段实施；M1～M3 已完成源码及隔离验证，M4 业务证据审核、冻结 Worker、矩阵与独立 8002 环境已实现。2026-11-01 r4 正式复验自动及助手非独立复核均 31/40，关键项 9/12，未达门槛，真实闭环仍待改进；r3 首轮 28/40、29/40 分别保留。详见阶段进度，不把模拟服务视为真实工厂接口。
- 制造业相关源码已合入主线；本机 8000 稳定版与 8001 候选版容器仍独立保留。Git 合并不代表容器已更新。
- M4 提示修正已在独立 8002 的 r4 镜像实测。用户新增授权最多 44 题，实际正式 40 题失败，条件性闭环 4 题未启动，不得挪作补跑；未自动扩大预算。
- 2026-11-06 源码增加后端字段展示及引用编译，并按已审核工单范围收窄运行时目录依赖；保留权限、相关新通知及来源变化校验。该轮仅隔离验证，未更新应用镜像或重跑付费问答，M4 正式验收状态不变。详见阶段进度。
- 同日后续将制造 HTTP 异常收敛至单一边界，并集中生成/修复的输出准备与校验；已知模型失败才回退，权限、来源、数据库、正式评测预算和编程错误直接终止。内部错误为脱敏 500，数据库不可用为 503，不把一般 `ValueError` 归为输入错误。具体验证见阶段进度，应用镜像及正式评分未随源码更新。
- 同日业务边界修复要求制造作业要求、版本对比及工单记录保留完整原句，后端标明要求/记录和所引版本；不再整理自写的工单主语。向量服务不可用明确返回 503，可选补查局部回退仍重新检查权限及来源。仅源码与隔离验证，正式评测和容器状态不变。
- 2026-11-07 增加服务端只读能力检查：明确执行请求在生成前拒绝，原问题与追问改写结果均检查；保留正常知识咨询和三次来源/权限校验。原前置验收的两项失败已原样复验通过；这不代表正式 40 题或真实改进闭环通过，旧失败及授权记录继续保留。运行版本以本轮验收记录为准。
- 同日独立 8002 API/模拟 MES 已更新为 `bc99254` 源码的 `phase1-capability-20261107-r1` 镜像，155 个业务 Python 文件与 Git 归档一致，完成 3 个固定拒答及 20 次来源读取的零模型核验。索引 Worker 保持停止，质量 Worker 未启动，未重建索引；8000/8001 保留。新 16 题开发预算仅为待批准草案，未发起供应商调用。详见阶段进度和脱敏运行报告。
- 先用 `git status --short`、`git log -1` 确认源码状态；涉及部署时再检查实际容器及镜像，不从旧提交号推断运行版本。

## 按需阅读

| 任务 | 入口 |
| --- | --- |
| 当前演示、启动、评分口径 | `docs/MANUFACTURING_DEMO.md` |
| 首期工单集成改造方案与验收标准 | `docs/MANUFACTURING_PHASE1_PLAN.md` |
| 首期分阶段实施进度与复现入口 | `docs/MANUFACTURING_PHASE1_PROGRESS.md` |
| 制造适用性审核、选版与检索边界 | `docs/MANUFACTURING_APPLICABILITY.md` |
| 制造问答、两类引用、工单会话与页面 | `docs/MANUFACTURING_QA.md` |
| 制造业务证据审核、冻结 Worker 与预算 | `docs/MANUFACTURING_QUALITY.md` |
| M4 实测结果、未通过项与隔离运行交付 | `docs/MANUFACTURING_M4_ACCEPTANCE.md` |
| M4 提示修正后的正式复验与授权结果 | `docs/MANUFACTURING_M4_REVALIDATION.md` |
| 制造业 98 题配置 | `evals/manufacturing_zh_lite_v1/README.md` |
| 稳定展示版本与冻结评分文件 | `evals/manufacturing_zh_lite_v1/demo_release.json` |
| 通用 120 题套件 | `evals/current/README.md` |
| 闭环实现与首轮验收 | `docs/MANUFACTURING_CLOSED_LOOP.md` |
| 反馈审核、样本导出、验证关闭 | `docs/QA_FEEDBACK.md`、`docs/QUALITY_REVIEW.md`、`docs/QUALITY_VERIFICATION.md` |
| 模块原理 | 按需读取 `docs/` 中对应专题；带日期的验收记录只代表该轮结果 |
| 本机私有产物 | `tmp/README.txt`（被 Git 忽略；新克隆可能不存在） |

## 源码定位

| 模块 | 位置 |
| --- | --- |
| API、浏览器工作台 | `backend/app/api/`、`backend/app/static/` |
| Keycloak 认证、租户与知识库授权 | `backend/app/security/` |
| 工单上下文、MES 连接与独立模拟服务 | `backend/app/manufacturing/`、`backend/app/integrations/`、`services/mock_mes/` |
| 文档解析、清洗、审核发布 | `backend/app/documents/` |
| 异步索引 | `backend/app/indexing/` |
| 检索、上下文、问答、路由及修复 | `backend/app/knowledge/` |
| 分层评测 | `backend/app/evaluation/`、`scripts/evaluate.py` |
| 反馈与改进闭环 | `backend/app/quality/` |
| 制造业运行与离线报告 | `scripts/manufacturing_workflow.py`、`scripts/report_manufacturing_demo.py` |

## 本机配置与权限

- Keycloak 负责认证；PostgreSQL 中的用户映射、部门成员和知识库授权决定访问范围，绑定依据是 Keycloak `sub`。
- 本机制造业演示已配置工艺、生产、质量三个部门。组织配置是运行数据库数据，不是新克隆或初始化脚本自动具有的默认值。
- 部门成员继承业务库授权；部门负责人身份与知识库 `admin` 权限分别维护，不把整个部门提升为 `admin`。
- 当前演示账号、令牌、租户和库 ID 保留在 `tmp/manufacturing-finalize-r1/`。只按任务读取所需项，不整份输出凭据，不写入提交或公开文档。

## 评测与修改边界

- 保留原始题集、评分规则、原始响应、Trace 和历史报告；不以新结果覆盖旧批次。
- 通用 120 题、制造业历史 120 题及精简 98 题分别记录。自动评分、表达等价辅助复核、超时补跑派生视图不合成独立准确率。
- `metrics.py` 承担冻结评分口径，`retrieval_metrics.py` 扩展固定 K 指标；不要因名称相近直接合并或替换。
- 部分评测文件按原始字节或 LF 归一化后的哈希校验；遵守 `.gitattributes` 和发布清单，文档整理不重写这些文件。
- 基线生成脚本、OCR 实验和历史资料仍可能被测试或复现入口引用；删除前检查调用关系。
- 沿用已有任务授权；没有相关任务要求时，不主动发起付费模型评测、重建索引或更新容器。
- 提交信息使用中文；提交和推送沿用会话中的有效授权。

## 常用验证

- 套件完整性：`.venv/Scripts/python.exe scripts/evaluate.py check`
- 代码改动先运行对应模块测试；确有需要时再完整回归。集成测试使用独立测试数据库。
- 仅修改说明文档时检查本地链接和代码路径，不重跑付费问答。
