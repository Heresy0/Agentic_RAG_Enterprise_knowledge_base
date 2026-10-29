# Multi-tenant Long-context RAG Engine

[![CI](https://github.com/Heresy0/Multi-tenant-long-context-rag-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/Heresy0/Multi-tenant-long-context-rag-engine/actions/workflows/ci.yml)

面向企业内部知识库的多租户 RAG 问答系统。项目使用 Keycloak 完成 OIDC 身份认证，在 PostgreSQL 中维护租户、部门、用户、知识库和授权关系，并通过 Milvus 实现带强制权限范围与当前索引版本过滤的向量检索。

系统已经覆盖文档上传、幂等索引、混合检索、重排、答案生成、引用校验、结构化观测日志和离线评估等核心链路，重点解决企业知识库中“用户是谁、能够查询哪些资料、回答依据来自哪里”的问题。

制造业小规模改造见[实施与验收说明](docs/MANUFACTURING_LITE.md)：提供 17 份四格式资料、20 道独立回归题、制造业示例问题和角色配置工具。[真实 HTTP 首轮验收](evals/manufacturing_lite/reports/20261028-live-r1/README.md)已完成隔离租户、全部入库及 20 题运行，自动评分 14/20、助手非独立复核 18/20，保留 2 题失败与 33 项 HTTP 权限检查。本轮未接 MES/ERP；[完整实施方案](docs/MANUFACTURING_IMPLEMENTATION_PLAN.md)保留后续工单集成设计。

新增[公开资料扩容v2](docs/MANUFACTURING_PUBLIC_V2.md)：60份资料包含20份NIST/NBS原始PDF、24个官方网页条目和16份合成业务资料，配套50道开发题与100道冻结验收题。已全部隔离入库，并完成[50题开发集真实首轮](evals/manufacturing_public_v2/reports/20261028-development-live-r1/README.md)：自动23/50，助手非独立复核32通过、13失败、1待确认、4上下文不完整；100题冻结验收集未运行。

新增[中文公开原文v3](docs/MANUFACTURING_ZH_V3.md)：18份官方中文原文（16份原始PDF、2份网页正文转换），配套120题中文评测集，开发40题、测试80题。[离线检查](evals/manufacturing_zh_v3/reports/20261029-offline-r1/README.md)得到1,290个实际分块，但150处证据锚点仍有44处未连续匹配，1份扫描件正文严重缺失；尚未入库或运行模型问答，既有基线保留原样。

已补充[PDF明确缺陷修复与回归对照](evals/manufacturing_zh_v3/reports/20261029-parser-fix-r1/README.md)：`structured-v6`修正误分栏、下标错行和数量开头正文误作标题，并阻断空OCR页的静默入库。原文、题集与首轮报告保留，困难扫描件识别仍需后续处理。

已完成[小规模离线OCR实验](evals/ocr_small_experiment_v1/reports/20261029-offline-r1/README.md)：增加3份独立扫描件，以固定300DPI＋空结果单次PSM6回退对照现有配置。独立检查10/27→11/27，旧扫描证据仍为25/55且有3处退化；收益不足，应用默认保持220DPI、不开启回退。原有语料、题集及报告不变。

中文资料后续已完成[新版本隔离入库与40题开发集真实首轮](evals/manufacturing_zh_v3/reports/20261029-development-live-r1/README.md)：17份就绪、1份空OCR阻断，1,034个分块与向量核验一致；自动30/40，助手非独立复核33/40（普通文档27/30、拒答6/6、澄清0/4）。冻结开发集没有OCR专项题，不报告OCR问答准确率。215个原有基线文件与旧租户资料保留，80题测试集未运行。

## 核心能力

文档入库前已接入[统一保守清洗](docs/DOCUMENT_CLEANING.md)：四种格式共用清洗模块并保留结构、来源页码、规则与前后指纹。清洗版本为 `conservative-v2`，索引解析版本为 `structured-v7`；旧报告保留原版本和原成绩，现有索引不会自动重建。

- Keycloak OIDC 登录、JWT 签名及 issuer、audience 校验
- 租户、部门、用户和知识库四层权限模型
- 用户授权与部门授权，支持 `viewer`、`editor` 和 `admin` 权限
- 不可变 `RetrievalScope`，检索时强制携带 `tenant_id` 和后端计算的授权知识库范围
- PostgreSQL 16 保存权限、正文与 BM25；Milvus 3.0.2 保存 1024 维向量并使用 COSINE / HNSW
- PDF、DOCX、TXT、Markdown 文档解析、切分和 1024 维向量入库
- 文件内容未变化时跳过重复入库，更新失败时保留旧版本
- Milvus 向量检索、pg_search 0.25.11 持久化 BM25、融合和重排
- 子块检索与重排、SQL 父块原文回取及带权限复核的全文引用预览
- 输入意图识别与有限问题拆分，自动选择普通、自检修复或 Agentic RAG
- 可回答性判断、资料引用和拒答机制
- 文档列表、上传、更新及删除 API
- 租户级问答限流、并发限制及文档数量/存储配额
- 问答阶段耗时、请求 ID 和权限拒绝事件等结构化日志
- Loki 集中日志、Grafana 日志检索和跨服务请求关联
- 事务内审计事件、数据库级防篡改触发器和管理员查询 API
- Worker 心跳、索引队列状态和分层健康检查
- Prometheus 指标、Alertmanager 通知和自动配置的 Grafana 看板
- 开发集、独立测试集和答案级离线评估脚本
- Locust 稳定负载、治理压力场景和自动性能基线门禁
- Docker Compose 本地环境及 GitHub Actions 持续集成
- 同源浏览器工作台，支持企业登录、知识问答、文档管理及索引任务状态查看
- 私有问答证据快照、点赞点踩、反馈列表及用户主动共享后的管理员访问，支持当前权限复核和会话删除级联清理

数据飞轮第一阶段的接口、权限边界、数据库迁移和使用步骤见[问答快照与用户反馈](docs/QA_FEEDBACK.md)。

第二阶段新增[质量工作台与回归样本](docs/QUALITY_REVIEW.md)：共享反馈审核、根因归类、标准与修订留痕、独立导出授权及版本化 JSONL；反馈或语料变化后须复核，不自动改动固定评测集。

第三阶段新增[评测与改进闭环](docs/QUALITY_VERIFICATION.md)：独立预算评测 Worker、固定集与反馈集对比、逐题人工复核、改进记录及关闭前的发布检查。评测须显式允许模型调用，Worker 默认不启动，检查不自动部署代码。

2026-10-28 已完成[三阶段本地运行验收](docs/QUALITY_RUNTIME_ACCEPTANCE_20261028.md)：真实认证、索引、问答及预算评测已贯通，并检查权限拒绝和验证关闭门禁；采用隔离租户与合成资料，不作为真实业务准确率或修复收益。

Milvus 的迁移、故障重试与备份恢复步骤见 [Milvus 运维说明](docs/MILVUS_MIGRATION.md)。

输入规划使用本地规则处理简单问题，复杂问题才尝试模型拆分；首轮检索后，
Agent 可决定补查或读取相邻原文。生成后的覆盖记录与引用绑定错误可进行一次有限修复，
不能改写事实或放宽最终校验。工具权限与可选模型调用共享请求预算。
首次生成发现明确证据缺口时，可定向补查一次并再生成一次；没有新增有效证据、权限变化或预算不足时停止。
配置和验证边界见 [输入路由与 Agent 证据收集说明](docs/AGENTIC_RAG.md)。

当前默认重排保留 12 个子块，再回取已授权且版本匹配的父块。上下文默认保留完整父块，仅在剩余字符预算不足时尝试一次受约束的模型章节选取；命中原文及规则识别出的条件、例外、列表和表格强制保留，无法完整放入时明确拒答。配置、回退及验证边界见[上下文构建说明](docs/CONTEXT_BUILDING.md)。
实现与升级步骤见 [父子块说明](docs/PARENT_CHILD_RAG.md)。最新整体评估与普通 RAG 对照结果见下文；
2026-10-14 的历史结果保留在 [父子块与回答修复验收](docs/PARENT_CHILD_ACCEPTANCE_20261014.md)。

演示与收尾验收步骤见 [演示验收指南](docs/DEMO.md)。项目当前面向功能演示和
技术方案验证，不将小样本评估结果或单机压测结果等同于生产级服务承诺。

## 量化效果与普通 RAG 对照

当前已统一为[120题评估套件](evals/current/README.md)，保留main100题、multihop12题、completeness8题各自成绩。以下区分2026-10-16最新汇总与历史批次，避免混用分母和评分口径。

### 最新120题回归汇总

120题真实运行原始r9成绩为107/120；修复CC-005否定等价表达误扣后，仅重跑该题并通过。最新汇总为108/120，助手逐题语义复核同为108/120；这是一轮120题加单题更新的汇总，不是另一轮120题全量生成。

| 分组 | 严格自动通过率 / 助手复核准确率 | 最终上下文标注证据召回 |
|---|---:|---:|
| 主回归100题 | 90%（90/100） | 100%（136/136） |
| 多跳12题 | 83.33%（10/12） | 100%（41/41） |
| 完整性8题 | 100%（8/8） | 100%（18/18） |
| 全部120题 | 90%（108/120） | 100%（195/195） |

104道可回答题正确92道（88.46%）；16道资料不足题全部正确拒答。12道未通过项包括11道误拒答和1道漏答关键事实。助手复核为非盲、非独立辅助审核；本套题为合成回归集，不能等同独立业务黄金集或生产泛化准确率。

见[原120题运行](evals/acceptance/current_120_20261016.json)、[CC-005修复与单题重跑](evals/acceptance/cc005_scoring_r10_20261016.json)、[120题助手复核](evals/acceptance/current_120_assistant_review_20261016.json)。后面的100题94%及12题对照为各自历史批次，保留原成绩。

### 100 题整体效果

使用 [100 题 v4](evals/archive/xinghai_v4/README.md)，包含 87 道可回答题与 13 道资料不足题。
该历史批次来自真实模型运行，并按r9规则对保存的原始回答重评分；重评分没有重新生成答案。

| 指标 | 当前结果 |
| --- | ---: |
| 严格自动通过率 | **94%（94/100）** |
| 可回答题严格通过率 | 93.1%（81/87） |
| 最终上下文必要证据召回率 | **100%（136/136）** |
| 必要证据完整的题目比例 | 100%（87/87） |
| 总拒答率 | 19%（19/100） |
| 资料不足题正确拒答率 | 100%（13/13） |
| 可回答题误拒答率 | 6.9%（6/87） |
| 原有 80 题通过率 | 97.5%（78/80） |
| 新增 20 题通过率 | 80%（16/20） |

剩余 6 道失败题最终均误拒答，涉及项目实体/意图理解、覆盖记录、派生数字计算和来源绑定，部分草稿也存在实际比较错误。
在这批题上，主要问题出现在证据取齐之后的理解、生成和校验环节。完整聚合结果及失败题分类见
[100 题最终评分记录](evals/acceptance/scoring_r9_100_20261016.json)。

### 12 道复杂多跳题对照

两组使用相同的 [12 道多跳题](evals/archive/xinghai_multihop/README.md)、经认证的评估账号、有效权限和语料；
实际可读 72 份文档、600 个子块、158 个父块。模型均为 `qwen3.7-flash`、`text-embedding-v4` 和 `qwen3-rerank`。
每题每组运行一次，交替执行先后顺序，共 24 个最终答案。

普通 RAG 基线关闭 Agent、查询规划、模型修复和反馈再生成，保留与当前项目相同的混合检索、Top-12 重排、父块上下文、生成提示、确定性补全和最终校验。
因此它是本项目的单次 RAG 消融基线，结果不能代表所有普通 RAG 实现。

| 指标 | 当前项目 | 普通 RAG 基线 | 差异（当前相对普通） |
| --- | ---: | ---: | ---: |
| 最终答案正确率（本次助手逐题复核） | **91.7%（11/12）** | 66.7%（8/12） | **+25 个百分点** |
| 严格自动通过率 | 91.7%（11/12） | 75.0%（9/12） | +16.7 个百分点 |
| 最终上下文必要证据召回率 | 100%（41/41） | 100%（41/41） | 相同 |
| 总拒答率 | 33.3%（4/12） | 50.0%（6/12） | −16.7 个百分点 |
| 可回答题误拒答 | 1/9 | 3/9 | 少 2 题 |
| 资料不足题正确拒答 | 3/3 | 3/3 | 相同 |
| 平均后端耗时 | 8.98 秒 | 6.27 秒 | **+43.1%** |
| 总报告 Token（含嵌入、重排） | 142,849 | 129,811 | **+10.0%** |
| 物理模型调用次数 | 45 | 36 | 多 9 次 |

差异集中在 `MH-005`、`MH-007`：普通链路因总体判定缺失或可回答状态矛盾被共同校验拒答，当前项目正确回答；
`MH-009`：普通链路比较方向及等号边界错误，自动评分漏检，复核后判错；
`MH-006`：两组均因实测数字和规则来源绑定错误而拒答，当前项目的一次修复仍未解决。

**本轮尚未验证多轮检索的收益。** 两组每题的融合候选、重排结果及最终上下文逐项相同；
当前组有 5 次规划、2 次 Agent 决策（均直接结束）和 2 次修复，没有实际补查、邻块读取或反馈再生成。
观察到的差异集中在答案组织、状态一致性和比较判断，不能从组合消融中单独归因于某个组件。
两组未知用量调用及用例执行错误均为 0；耗时不含 HTTP 网关和初始化，跨模型相加的 Token 不等同于金额。
聚合记录见 [多跳对照结果](evals/acceptance/multihop_comparison_20261016.json)，逐题分析及运行方法见
[分层评估说明](evals/SYSTEM_EVALUATION.md#8-12题复杂多跳成对对照2026-10-16)。

以上为同源合成回归题的测量结果。100 题成绩是自动评分代理，未独立人工核验所有结论；
12 题复核由本次助手完成，单次运行存在模型输出波动，不能据此推断生产环境或未见问题上的稳定准确率。

## 系统架构

```mermaid
flowchart LR
    U[用户或业务系统] -->|OIDC 登录| K[Keycloak]
    K -->|JWT Access Token| U
    U -->|Bearer Token| A[FastAPI]
    A --> I[身份认证与权限校验]
    I --> S[RetrievalScope]
    S --> Q[输入规划与策略路由]
    Q --> R[混合检索与重排]
    R --> P[(PostgreSQL + pg_search)]
    R --> V[(Milvus)]
    R --> L[大模型服务]
    A --> D[文档管理服务]
    D --> F[(受管文件目录)]
    D --> J[(文档索引任务)]
    J --> W[索引 Worker]
    W --> F
    W --> P
    W --> V
    A --> O[结构化日志与耗时指标]
    A --> AU[(不可变审计事件)]
    O --> M[Prometheus]
    O --> AL[Grafana Alloy]
    AL --> LK[Loki]
    LK --> G[Grafana]
    M --> G[Grafana]
    M --> AM[Alertmanager]
    AM --> AW[Webhook 通知网关]
    AW -. 可选转发 .-> E[外部告警平台]
```

一条问答请求的大致流程为：

1. API 验证 Keycloak JWT，并把外部身份映射为本地租户用户。
2. 授权服务验证用户对目标知识库的访问权限。
3. 系统生成不可变的 `RetrievalScope`。
4. 多轮问题补全后，识别意图、按需拆分并建议执行策略；原问题保留。
5. SQL 读取已授权且 ready 的索引版本，Milvus 在 ANN 前过滤租户、知识库和版本，SQL 回查再次核验命中。
6. 系统完成混合检索、融合和重排，必要时由 Agent 补查或补读。
7. 重排子块回取当前授权父块并去重，在原字符及来源预算内优先保留完整父块；预算紧张时可有限模型选段，复杂问题保留完整父块，完整证据放不下时拒答。
8. 大模型根据受控上下文生成答案；结构和引用绑定错误可有限修复，最终校验通过才输出引用。

## 技术栈

| 类别 | 技术 |
| --- | --- |
| Web API | FastAPI、Pydantic、Uvicorn |
| 身份认证 | Keycloak、OIDC、JWT |
| 数据库 | PostgreSQL 16、SQLAlchemy、Alembic |
| 向量检索 | Milvus 3.0.2、REST v2、COSINE / HNSW |
| RAG | LangChain、BM25、DashScope Embedding/Rerank/LLM |
| 文档处理 | PyPDF、pdfplumber、python-docx、Tesseract OCR |
| 测试 | pytest、真实 PostgreSQL 集成测试 |
| 工程化 | Docker Compose、GitHub Actions |
| 可观测性 | JSON 日志、Grafana Alloy、Loki、Prometheus、Alertmanager、Grafana |

## 目录结构

```text
.
├── backend/app/              # FastAPI、认证授权、检索和问答服务
│   ├── api/                  # REST API 路由
│   ├── db/                   # ORM 模型、数据库会话及初始化逻辑
│   └── security/             # OIDC、Principal、权限与检索范围
├── migrations/               # Alembic 数据库迁移
├── scripts/                  # 租户初始化、批量入库和评估工具
├── tests/                    # 单元测试、API 测试和集成测试
├── evals/
│   ├── datasets/             # 开发集和独立测试集
│   └── reports/              # 评估报告
├── sample_docs/              # 演示文档
├── monitoring/               # Prometheus、Alertmanager、Alloy、Loki 与 Grafana 配置
├── compose.yaml              # 应用、依赖服务与监控栈
├── Dockerfile                # API 生产镜像入口
└── requirements.txt
```

## 快速开始

### 1. 环境要求

- Git
- Docker Desktop，支持 Docker Compose
- 可选：Python 3.12，用于不通过容器运行 API 和测试
- 可用的对话、Embedding 和 Rerank 模型服务

### 2. 获取代码

```powershell
git clone https://github.com/Heresy0/Multi-tenant-long-context-rag-engine.git
Set-Location Multi-tenant-long-context-rag-engine
```

### 3. 配置环境变量

```powershell
Copy-Item .env.example .env
python scripts/initialize_milvus_secrets.py
```

编辑 `.env`，至少设置以下配置：

- PostgreSQL 数据库名、用户名和密码
- Keycloak 管理员用户名和密码
- OIDC issuer、audience 和 JWKS 地址
- DashScope 对话、Embedding 和 Rerank 模型配置

应用启动时会校验必要环境变量，空值会导致启动失败。不要把包含真实密钥的 `.env` 提交到 Git。

Milvus 密钥保存在被 Git 和 Docker 构建排除的 `.secrets/milvus/`，不输出到终端。
一次性 `milvus-init` 服务修改默认 root 密码、初始化集合及只读 API / 读写 Worker 账号，并执行 SQL 迁移和旧向量导入。
API 和 Worker 仅挂载各自的服务密钥，不持有 Milvus 管理员凭据。业务用户仍然通过 Keycloak 登录。
现有部署启用步骤与密钥维护见 [Milvus 运维说明](docs/MILVUS_MIGRATION.md#服务认证与账号权限)。

### 4. 启动基础服务

首次运行时，先启动 PostgreSQL 和 Keycloak：

```powershell
docker compose up --build -d postgres keycloak
docker compose ps
```

访问 Keycloak 管理后台：

- 地址：<http://127.0.0.1:8080>
- 管理员账号：使用 `.env` 中的配置

在 Keycloak 中创建以下开发环境资源：

- Realm：`enterprise-knowledge`
- Client：`enterprise-knowledge-api`
- Client audience：`enterprise-knowledge-api`
- 测试用户，例如 Alice 和 Bob

本地用户通过 Keycloak Token 中的 `sub` 与数据库中的 `external_subject` 关联。

### 5. 启动 API 和索引 Worker

Keycloak Realm 和模型配置准备完成后，构建并启动全部服务：

```powershell
docker compose up --build -d
docker compose ps
```

检查服务：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

正常响应：

```json
{
  "status": "ok"
}
```

常用地址：

- Swagger API 文档：<http://127.0.0.1:8000/docs>
- OpenAPI JSON：<http://127.0.0.1:8000/openapi.json>
- Keycloak：<http://127.0.0.1:8080>
- Prometheus：<http://127.0.0.1:9090>
- Alertmanager：<http://127.0.0.1:9093>
- 告警 Webhook 网关状态：<http://127.0.0.1:8090/status>
- Loki 就绪状态：<http://127.0.0.1:3100/ready>
- Alloy 组件状态：<http://127.0.0.1:12345>
- Grafana：<http://127.0.0.1:3000>

查看 API、索引 Worker 和告警通知日志：

```powershell
docker compose logs -f api worker alloy loki alertmanager alert-webhook
```

健康检查地址：

- `/health/live`：API 进程存活检查
- `/health/ready`：数据库就绪检查
- `/health/indexing`：Worker 心跳与索引队列状态

Worker 默认每 10 秒写入一次心跳，30 秒没有新心跳即视为不可用。
可以通过 `INDEXING_WORKER_HEARTBEAT_SECONDS` 和
`INDEXING_WORKER_STALE_SECONDS` 调整，但失联阈值至少应为心跳间隔的两倍。

### Prometheus、Loki、Alertmanager 与 Grafana

API 在 `/metrics` 导出 Prometheus 文本指标。指标标签只包含请求路由、
状态和处理结果等低基数字段，不包含租户、用户、问题、文档或任务标识。
租户上传配额拒绝通过
`enterprise_tenant_upload_quota_rejections_total{resource}` 统计，
`resource` 仅区分 `documents` 与 `storage_bytes`，不会产生高基数标签。

Prometheus 每 15 秒抓取一次 API，并加载以下告警：

- 索引 Worker 持续不可用
- 排队任务持续超过十个
- 最近十分钟出现最终失败任务
- QA P95 延迟持续超过十五秒

Prometheus 把触发的告警发送给 Alertmanager。Alertmanager 负责分组、
去重、抑制、重复通知和恢复通知，再发送到内部 Webhook 网关。严重告警
等待 5 秒后发送，每 30 分钟重复一次；普通告警等待 10 秒后发送，默认
每 4 小时重复一次。同名 `critical` 告警会抑制对应的 `warning` 告警。

Webhook 网关默认进入本地接收模式，只输出结构化事件和低基数指标，
不保存告警正文。需要转发到接受 Alertmanager JSON 的企业告警平台时，
先创建只供网关读取的独立环境文件：

```powershell
Copy-Item .env.alerting.example .env.alerting
```

然后在 `.env.alerting` 中配置：

```env
ALERT_WEBHOOK_FORWARD_URL=https://alerts.example.com/hooks/your-id
ALERT_WEBHOOK_AUTHORIZATION=Bearer your-token
ALERT_WEBHOOK_TIMEOUT_SECONDS=10
```

`ALERT_WEBHOOK_AUTHORIZATION` 是可选项。不要把真实 URL、令牌或密码提交
到 Git；它们只应保存在本地 `.env.alerting` 或部署平台的密钥管理服务中。外部
端点返回非 2xx 或连接失败时，网关返回 502，让 Alertmanager 自动重试。

Grafana 启动时会自动配置 Prometheus、Loki 两个数据源，以及
`Enterprise Knowledge RAG`、`Enterprise Knowledge Logs & Audit` 两个看板。
首次启动前应在 `.env` 中修改：

```env
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=<强密码>
```

Alloy 通过只读 Docker Socket 发现本项目容器，把日志发送到 Loki。
Loki 在本地保留 7 天日志；`service`、`container`、`environment` 等低基数
字段作为标签，用户、租户、知识库和 `request_id` 不作为标签。需要按请求
排障时，在 Grafana Explore 中使用：

```logql
{stack="enterprise-knowledge", service="api"}
| json
| request_id="<响应头中的 X-Request-ID>"
```

查看已经成功提交到数据库的审计事件：

```logql
{stack="enterprise-knowledge", service="api"}
| json
| event="audit.committed"
```

Loki 本身不负责业务审计的永久保存。权威记录存放在 PostgreSQL
`audit_events` 表中；Loki 中的副本用于搜索、关联和排障。Loki 和 Alloy
端口仅绑定 `127.0.0.1`。生产环境应把 Loki 放在私有网络或认证代理后，
并使用受控的日志采集权限。

### 不可变审计日志

系统对以下安全敏感动作写入审计事件：

- 文档上传并创建异步索引任务
- 文档删除
- 最终失败索引任务的人工重试
- 问答访问成功、拒绝、参数无效或执行失败

上传、删除和重试的审计事件与业务变更使用同一数据库事务。ORM 事件监听器
阻止应用代码更新或删除审计记录，Alembic 迁移还会在 PostgreSQL 中创建
`BEFORE UPDATE OR DELETE` 触发器，防止绕过 ORM 的直接 SQL 篡改。

审计详情采用白名单式最小记录原则：只保存版本号、索引任务 ID、是否可回答、
引用数量等必要字段，不保存 JWT、问题、答案、文档正文、本地路径或存储 URI。
事件保留原始 `tenant_id`、`actor_user_id` 和资源 ID 快照，不使用指向业务表的
级联外键，因此删除业务资源后审计证据仍然存在。

常用指标包括：

```text
enterprise_http_requests_total
enterprise_qa_requests_total
enterprise_qa_request_duration_seconds
enterprise_qa_stage_duration_seconds
enterprise_indexing_jobs_total
enterprise_indexing_job_duration_seconds
enterprise_indexing_workers_active
enterprise_indexing_queue_jobs
enterprise_indexing_oldest_queued_seconds
enterprise_alert_webhook_deliveries_total
enterprise_alert_webhook_alerts_total
alertmanager_notifications_total
alertmanager_notifications_failed_total
```

API 指标由 `api:8000/metrics` 提供；索引执行计数和耗时由 Worker
内部的 `worker:9101/metrics` 提供。Worker 指标端口只暴露在 Compose
内部网络，不映射到宿主机。

Alertmanager 和 Webhook 网关分别监听 `9093` 和 `8090`。本地端口只绑定
到 `127.0.0.1`；生产部署时也应通过防火墙、反向代理或私有网络限制访问。

可以随时执行一次不会污染业务数据的通知链路冒烟测试：

```powershell
python scripts/test_alert_pipeline.py
```

脚本会向本地 Alertmanager 注入一条临时严重告警，等待网关接收后输出
`"status": "passed"`，并在退出前自动把该测试告警标记为已恢复。

停止服务：

```powershell
docker compose down
```

`docker compose down` 不会删除数据库和文档卷。只有明确需要清空本地数据时，才使用 `docker compose down --volumes`。

## 浏览器工作台

API 启动后打开 <http://127.0.0.1:8000/>，即可使用内置的“知序”工作台。
页面使用原生 HTML / CSS / JavaScript，由 FastAPI 同源提供，Docker 镜像自动包含
静态资源，无需 Node.js、前端构建或额外容器。`3000` 端口仍为 Grafana。

- 选择当前账号有权访问的知识库，提交问题并查看答案、引用和耗时；点击引用可预览对应分块原文。每次预览重新校验知识库权限，文档更新或删除后已失效的引用返回 404。
- 查看文档的索引状态、版本、分块数量和更新时间。
- editor / admin 可上传和删除文档、查看索引任务、重试失败任务。
- 上传返回 202 后，页面每 5 秒刷新活动任务；任务完成后文档可用于问答。
- viewer 仅可查看和提问。所有权限仍由 API 校验，前端显示不替代服务端授权。

初次体验可点击“登录工作空间”，展开“已有访问令牌？临时登录”，粘贴现有
Alice / Bob Access Token。令牌仅保存在页面内存中，刷新后需要重新登录；过期时会
提示重新登录。问题和答案也只保留在当前页面，切换知识库或退出时清空。

使用企业账号直接登录前，需要在 Keycloak 的 `enterprise-knowledge` Realm
创建一个浏览器 public client：

1. Client ID：`enterprise-knowledge-web`（对应 `.env` 中可选的
   `OIDC_FRONTEND_CLIENT_ID`，默认使用此名称）。
2. Client authentication：Off；Standard flow：On；Implicit flow 和 Direct access grants：Off。
3. Valid redirect URIs：`http://127.0.0.1:8000/`。
4. Valid post logout redirect URIs：`http://127.0.0.1:8000/`。
5. Web origins：`http://127.0.0.1:8000`。
6. 当前 Keycloak 26.7.4 界面：Settings → Capability config → Require PKCE：On，
   PKCE Method：`S256`，保存客户端配置。其他版本可能使用不同字段名称。
7. Client scopes → `enterprise-knowledge-web-dedicated` → Mappers →
   Configure a new mapper（已有映射时从 Add mapper 菜单进入）→ Audience。
   Name 可填 `enterprise-api-audience`；Included Client Audience 必须从下拉列表
   **点击选中** `enterprise-knowledge-api`（与 `OIDC_AUDIENCE` 一致），
   Included Custom Audience 留空，Add to access token：On，Add to ID token：Off。
   保存后重新打开映射，确认 Audience 的值不是空白。

以上 public client、重定向与来源配置遵循
[Keycloak 浏览器应用说明](https://www.keycloak.org/securing-apps/javascript-adapter)。
前端使用授权码 + PKCE S256，不接收用户密码、不使用 client secret；Keycloak 返回
Token 后仍由原有 JWT 验证和数据库权限链路鉴权。浏览器登录支持自动续期，Token
仅在内存中保存，sessionStorage 只暂存一次性登录 state 和 PKCE verifier。
生产环境需将登录回调、登出回调、Web origins 和 `OIDC_ISSUER` 改为对应 HTTPS 地址。

若登录回到工作台后仍出现“访问令牌无效或已过期”，该提示也可能表示 audience、
issuer 或签名公钥校验失败，不一定是真的过期。优先核对上述 Audience 映射；修改
映射后必须刷新页面并重新登录以取得新令牌，不要关闭后端校验或粘贴令牌到公共网站。

更新 Docker 页面与 API：

```powershell
docker compose up -d --build api worker
```

本机 `--reload` 运行时刷新页面即可。前端接口及安全头测试可执行：

```powershell
python -m pytest tests/test_frontend_api.py -q
```

若本机已有 Node.js 24，可额外运行无依赖的登录逻辑测试（CI 也会执行）：

```powershell
node --test tests/frontend/auth.test.mjs
```

Node.js 仅用于开发测试；部署和运行工作台不需要安装 Node.js。

## 不使用容器运行 API

PostgreSQL 和 Keycloak 仍可通过 Docker 启动，API 在本机虚拟环境中运行：

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m alembic upgrade head
python -m uvicorn backend.app.main:app --reload
```

本机运行时，`.env` 中的数据库和 Keycloak 地址应使用 `127.0.0.1`，而不是 Compose 服务名。

## 初始化租户与权限数据

先从 Keycloak 用户详情或访问令牌的 `sub` 字段取得用户 subject，然后执行初始化脚本。

创建首个租户、部门、管理员和知识库：

```powershell
python scripts/bootstrap_tenant.py `
  --tenant-name "示例企业" `
  --department-name "技术部" `
  --subject "<Alice 的 Keycloak sub>" `
  --user-name "Alice" `
  --user-email "alice@example.com"
```

为已有租户创建另一个部门负责人：

```powershell
python scripts/provision_department_manager.py `
  --tenant-name "示例企业" `
  --department-name "人力资源部" `
  --subject "<Bob 的 Keycloak sub>" `
  --user-name "Bob" `
  --user-email "bob@example.com"
```

脚本会输出租户、用户、部门和知识库 UUID，后续入库与接口验证会使用这些 ID。

如果 API 仅在容器中安装了依赖，也可以把命令改为：

```powershell
docker compose exec api python scripts/bootstrap_tenant.py --help
```

## 文档入库

系统支持 `.pdf`、`.docx`、`.txt` 和 `.md` 文件。

TXT 先清理 UTF-8 BOM、空字符与换行，按空行分段，识别明确的编号标题和
`Q1`、`Q:`、`问：` 问答对；长段落使用 500 字、50 字重叠的二次切分。
Markdown 按 `#` 标题和 Setext 标题建立章节路径，代码围栏内不识别标题；
代码块保留缩进与语言标记，超长代码按行切分并为每块补完整围栏。表格按行
切分并重复表头，列表条目与缩进说明尽量一起保留。TXT、Markdown 和 DOCX
复用最终分块生成逻辑，统一附加文档名、章节、内容类型和父单元标识。

当前切分版本为 `structured-v4`。版本升级不会自动重建已有索引，需要重新
提交入库任务；再次入库时版本检查会使旧版本分块重新生成。

PDF 入库采用渐进式处理：先清理重复页眉、页脚和页码，按标题与段落进行
跨页切分，并为分块保留章节和起止页码；检测到表格或多栏内容时使用布局
解析；页面只有图片而没有足够文本时自动使用 OCR。相关运行参数包括：

- `PDF_LAYOUT_ENABLED`：是否启用表格和多栏解析，默认启用。
- `PDF_OCR_ENABLED`：是否对图片页启用 OCR，默认启用。
- `PDF_OCR_LANGUAGE`：Tesseract 语言，默认 `chi_sim+eng`。
- `PDF_OCR_MIN_CHARACTERS`：低于该文本字符数时考虑 OCR，默认 `20`。
- `POPPLER_PATH`、`TESSERACT_CMD`：非标准安装位置下的可执行文件路径。

OCR 只会用于低文本量且包含图片的页面，普通文本型 PDF 不承担 OCR 开销。

### 通过 API 上传

取得访问令牌后，可以在 Swagger 页面点击 **Authorize**，然后调用：

```text
POST /api/knowledge-bases/{knowledge_base_id}/documents
```

上传操作要求当前用户至少具有目标知识库的 `editor` 权限。
接口完成安全暂存和任务创建后返回 `202 Accepted`，响应中的
`indexing_job.id` 是后续查询索引进度的任务标识。文件在任务成功前
不会覆盖已有的正式版本。

每个租户默认最多保存 1000 个文档、占用 10 GiB 文档空间。配额以
PostgreSQL 为权威账本，同时计算正式文档和仍在排队、执行或失败保留期内的
候选文件。上传事务会锁定租户记录，因此多个 API 实例同时接收上传也不会
超卖额度。超额请求返回 `409 Conflict`，并通过
`X-Tenant-Quota-Resource`、`X-Tenant-Quota-Limit` 和
`X-Tenant-Quota-Used` 响应头说明触发的限制。

可以直接为不同租户配置不同额度，例如：

```sql
UPDATE tenants
SET max_document_count = 2000,
    max_storage_bytes = 21474836480
WHERE id = '<tenant UUID>';
```

索引成功后，候选文件预留会转换为正式文档占用；最终失败的候选文件在保留期
内仍占用额度，Worker 清理过期候选文件后自动释放。删除文档时，相关正式占用
和任务预留随数据库级联删除自动释放。历史命令行入库文档可再次运行相同入库
命令，以幂等方式回填 `file_size_bytes`，不会重复生成向量。

索引任务由独立 Worker 执行。可以使用以下接口查询任务状态：

```text
GET /api/knowledge-bases/{knowledge_base_id}/indexing-jobs/{job_id}
```

任务状态依次为 `queued`、`running`，最终进入 `succeeded` 或
`failed`。临时失败会按照任务的最大尝试次数自动重试。
编辑者还可以查询知识库最近的任务，并按状态或文档过滤：

```text
GET /api/knowledge-bases/{knowledge_base_id}/indexing-jobs
GET /api/knowledge-bases/{knowledge_base_id}/indexing-jobs?status=failed
```

最终失败任务的候选文件默认保留 24 小时。在保留期内排除故障后，
可以通过以下接口把任务重新加入队列：

```text
POST /api/knowledge-bases/{knowledge_base_id}/indexing-jobs/{job_id}/retry
```

Worker 还会回收无任务引用的 `.staging`、`.backup-*` 和 `.deleting-*` 临时文件，默认保留 24 小时（`INDEXING_ORPHAN_FILE_RETENTION_HOURS`）。活动任务、仍被任务引用的候选文件及正式文件缺失时可能用于恢复的备份会被保留；清理仅在受管知识库目录内进行。

保留时间由 `INDEXING_FAILED_FILE_RETENTION_HOURS` 控制。超过保留期的
候选文件会由 Worker 清理，此后需要重新上传原文档。

### 通过命令行写入单个文件

```powershell
python scripts/index_documents.py `
  --file "sample_docs/example.docx" `
  --tenant-id "<tenant UUID>" `
  --knowledge-base-id "<knowledge base UUID>" `
  --user-id "<user UUID>"
```

### 批量写入目录

```powershell
python scripts/index_documents.py `
  --directory "sample_docs" `
  --recursive `
  --tenant-id "<tenant UUID>" `
  --knowledge-base-id "<knowledge base UUID>" `
  --user-id "<user UUID>"
```

入库服务会校验 1024 维向量，使用事务替换旧分块，并根据内容和索引配置判断是否跳过重复入库。

## API 概览

所有 `/api` 接口都需要：

```http
Authorization: Bearer <access_token>
```

| 方法 | 路径 | 最低权限 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/health` | 无 | 健康检查 |
| `GET` | `/health/live` | 无 | API 进程存活检查 |
| `GET` | `/health/ready` | 无 | 数据库就绪检查 |
| `GET` | `/health/indexing` | 无 | Worker 心跳和索引队列状态 |
| `GET` | `/metrics` | 无 | Prometheus 指标，仅建议在内网暴露 |
| `GET` | `/api/knowledge-bases` | 已登录 | 列出当前用户可访问的知识库 |
| `GET` | `/api/knowledge-bases/{knowledge_base_id}/documents` | viewer | 列出知识库文档 |
| `POST` | `/api/knowledge-bases/{knowledge_base_id}/documents` | editor | 暂存文档并创建异步索引任务 |
| `GET` | `/api/knowledge-bases/{knowledge_base_id}/indexing-jobs` | editor | 列出并过滤文档索引任务 |
| `GET` | `/api/knowledge-bases/{knowledge_base_id}/indexing-jobs/{job_id}` | editor | 查询文档索引任务状态 |
| `POST` | `/api/knowledge-bases/{knowledge_base_id}/indexing-jobs/{job_id}/retry` | editor | 人工重试最终失败任务 |
| `DELETE` | `/api/knowledge-bases/{knowledge_base_id}/documents/{document_id}` | editor | 删除文档、分块和受管文件 |
| `GET` | `/api/knowledge-bases/{knowledge_base_id}/audit-events` | admin | 查询租户隔离的知识库审计事件 |
| `POST` | `/api/qa` | viewer | 默认在全部授权知识库内问答，也可指定单个知识库 |

问答请求示例：

```json
{
  "knowledge_base_id": "8e4af664-22c1-480e-9393-36ac401da0f5",
  "question": "开放平台访问令牌默认有效期是多少？"
}
```

成功响应包含答案、是否可回答、引用、拒答原因和各处理阶段耗时。响应头中的 `X-Request-ID` 可用于关联服务端结构化日志和审计事件。

审计查询支持 `action`、`outcome`、`before` 和 `limit` 参数。接口同时强制
校验当前租户、知识库和 `admin` 权限，普通 `viewer`、`editor` 不能读取。

## 测试

运行单元测试和 API 测试：

```powershell
python -m pytest -q
```

运行真实 PostgreSQL 与 Milvus 集成测试（使用名称以 `_test` 结尾的独立测试库）：

```powershell
$env:RUN_POSTGRES_INTEGRATION_TESTS = "1"
$env:MILVUS_INTEGRATION_ADMIN_TOKEN_FILE = ".secrets/milvus/admin_token"
python -m pytest tests/test_milvus_integration.py -q
Remove-Item Env:RUN_POSTGRES_INTEGRATION_TESTS
Remove-Item Env:MILVUS_INTEGRATION_ADMIN_TOKEN_FILE
```

运行编译检查：

```powershell
python -m compileall backend scripts
```

GitHub Actions 会在 Pull Request 和 `main` 分支推送时自动执行：

1. 启动 PostgreSQL / pg_search、Milvus / etcd / MinIO 服务。
2. 安装开发依赖。
3. 编译 Python 源码。
4. 执行 Alembic 数据库迁移。
5. 运行完整 pytest 测试。
6. 构建 API Docker 镜像。

## 性能压测与容量基线

项目提供 [Locust 场景](tests/load/locustfile.py) 和自动报告脚本，直接访问正式
OIDC、权限、限流、并发控制和 QA 链路。支持两种模式：

- `steady`：建立正常负载下的吞吐量、P50、P95、P99、错误率基线；429 算失败。
- `governance`：刻意超过租户并发上限；429 算预期结果，5xx 仍然算失败。

新版入口 `scripts/run_load_test.py` 使用固定的简单、复杂多跳和拒答负载，支持全部授权知识库、发出请求前的数量上限、父块引用预览及分阶段耗时。合法 HTTP 响应与可回答性匹配分别检查；通过现有 Prometheus 计数差值观察规划、Agent、修复、反馈和预算行为，不增加监控组件。真实调用需有效 `.env.evaluation` 令牌及显式 `--allow-model-calls`，配置可先用 `--dry-run` 离线检查。
当前版本实测、工具误判及未覆盖路径见 [新版压测验收记录](docs/LOAD_TEST_ACCEPTANCE_20261014.md)；保留原始组合门禁失败结果，不将独立预览回查改写成新的完整压测通过。

2026-10-14 的小规模 HTTP 混合负载中，24 次问答均返回 200，无 5xx 或限流；中位耗时 4.61 秒，P95 为 8.52 秒。
这是历史小样本观测，未建立生产并发容量，也不能与上方本地后端对照耗时直接比较。数据见
[压测聚合记录](evals/acceptance/load_test_20261014.json)。

原始 Locust CSV 和 HTML 写入 `data/load-tests`，不会提交到 Git。汇总脚本会
生成不包含 JWT、问题正文或租户标识的 JSON 报告，并检查最小请求数、失败率、
P95、5xx 数量以及治理场景是否真的出现 429。任一门槛不通过时脚本返回非零
退出码，可直接用作持续集成或发布门禁。

完整准备步骤、PowerShell 命令和推荐参数见
[tests/load/README.md](tests/load/README.md)。

## 答案级评估

统一入口为`python scripts/evaluate.py`，默认使用[当前120题套件](evals/current/README.md)。主回归100题、多跳12题和完整性8题分别评分；多轮8组24轮、权限12项与压测负载单独统计。旧题集已移至[冻结归档](evals/archive/README.md)，历史报告保留原样。

新增 `ranking` 首位排序门禁、Hit@1/3/5、模型原始排名与规则干预诊断，以及 `semantics` 条件/否定/来源断言和绑定实际上下文的独立复核。原 r10 自动代理分继续单独报告；未复核不能当成语义正确。用法与回归范围见[排序与语义评估说明](docs/EVALUATION_QUALITY.md)。

```powershell
python scripts/evaluate.py check
python scripts/evaluate.py system --mode offline --layers parsing --ocr --strict
python scripts/evaluate.py system --mode live --config evals/system_config.local.json --layers retrieval,rerank,context,answer --allow-model-calls --save-traces
# 可加 --group main / --group multihop / --group completeness；--group可重复
```

真实运行需要现有有效JWT及授权。`report.json`的`group_scores`与Markdown分组表保留各组质量指标；题目、事实标准和权限标注未改。Ragas默认复用main组保存的100题答案，压测使用统一套件中的8题负载。

以下保留旧HTTP答案入口的历史兼容用法，需要有效JWT：

```powershell
$env:ENTERPRISE_KB_ACCESS_TOKEN = "<JWT>"

python scripts/evaluate_answers.py `
  --dataset evals/archive/datasets/answer_test.jsonl `
  --knowledge-base-id "<knowledge base UUID>" `
  --category technical `
  --output evals/reports/answer_test_technical.json

Remove-Item Env:ENTERPRISE_KB_ACCESS_TOKEN
```

评估报告包含：

- 可回答性准确率和混淆矩阵
- 可回答问题接受率
- 不可回答问题拒答率与误接受率
- 必要事实覆盖率与事实完整率
- 引用来源准确率、召回率和命中率
- 总耗时及检索、重排、生成等阶段的 P50/P95 耗时

历史独立技术测试集包含 11 条样本。旧报告中，可回答性准确率为 100%，必要事实覆盖率为 92.86%，引用来源准确率和召回率均为 100%；它不是当前版本的完整验收结果。
当前套件中的main组来自[原100题v4](evals/archive/xinghai_v4/README.md)：保留原80题，增加20道南栖题，共87道可回答、13道资料不足。
新增 [12 份南栖业务文档](sample_docs/xinghai_nanqi/README.md) 已按四个知识库各 3 份入库；2026-10-16 本地授权范围内共有 72 份 ready 文档。
运行 `python scripts/build_100_evaluation.py --prepare-corpus` 准备 68 份指定评估原文（其余为未列入清单的旧资料）。
原80题、多跳12题与历史报告继续保留。历史100题批次按r9规则严格自动通过94/100，最终上下文标注证据召回136/136，资料不足题正确拒答13/13；另有6/87道可回答题最终拒答。见[历史100题最终评分记录](evals/acceptance/scoring_r9_100_20261016.json)；当前120题汇总见上方最新表。首轮89/100的[历史快照](evals/acceptance/current_100_20261016.json)保留原样；题集、语料或评分口径不同的总体分数不能直接比较。

可选的 [Ragas旁路评估](docs/RAGAS_EVALUATION.md)复用100题最终答案和实际上下文，不进入问答运行链路。2026-10-16补全参考主体及必要背景、单列必答与可选信息后，同批答案的事实F1均值由0.4235升至0.6851，忠实度由0.9224变为0.9247；诊断筛查通过由16/100变为43/100，与原规则的差异由78题减至51题。答案、上下文及r9事实标准未变，原自动通过仍为94/100；F1不是准确率，残留正确扩展扣分、裁判拆解和少量业务措辞/引用风险。见 [原轮次记录](evals/acceptance/ragas_saved_100_20261016.json)与[完整参考对照](evals/acceptance/ragas_reference_comparison_100_20261016.json)。
历史结果与已知问题见 [2026-10-14 验收记录](docs/PARENT_CHILD_ACCEPTANCE_20261014.md)，扩容收据见 [2026-10-16 扩容快照](evals/acceptance/nanqi_expansion_20261016.json)。

## 权限与数据隔离

权限隔离不是只在 API 路由中完成。核心检索仓储要求调用方提供不可变的 `RetrievalScope`，数据库查询同时使用：

- `tenant_id`
- `knowledge_base_id`

因此，即使上层误传文档 ID，底层检索也不会跨租户或跨知识库返回数据。现有测试覆盖 Alice、Bob 分属不同部门时的知识库访问隔离，以及跨知识库向量检索隔离。

## 文档生命周期

- 同一知识库中的同名文件再次上传时视为更新。
- 文件内容和索引配置未变化时跳过重复入库。
- 更新成功后文档版本号递增。
- 更新过程使用事务替换分块；向量生成或写入失败时保留旧版本。
- 删除接口同时清理数据库文档记录、向量分块和受管文件。
- 删除服务只允许删除受管文档目录中的文件，不会删除原始样例文件。

## 多轮对话

知识问答页面支持新建、恢复和删除私人对话。对话按租户、用户和检索模式隔离；单库对话仍绑定原知识库，全范围对话单独保存。切换范围或退出登录会清除页面中的当前对话。重新登录后可从“最近对话”恢复历史。

启用前先迁移数据库，再重启 API 服务：

```powershell
python -m alembic upgrade head
```

接口使用方法：

1. `POST /api/knowledge-bases/{knowledge_base_id}/conversations` 创建对话，得到 `id`。
2. `POST /api/qa` 传入 `knowledge_base_id`、`question` 和 `conversation_id`，后续追问复用同一 ID。
3. `GET /api/knowledge-bases/{knowledge_base_id}/conversations` 获取当前用户最近的对话（默认 20 条，支持 `limit` / `offset`）。
4. `GET /api/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}` 恢复历史（默认最近 100 轮，支持 `limit` / `before`；`next_before` 用于继续读取更早的记录）。
5. `DELETE /api/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}` 删除对话及其全部历史。

不传 `conversation_id` 的 `/api/qa` 请求仍是原来的单轮问答，不保存历史，保持原响应字段。多轮响应增加 `conversation_id` 和 `conversation_context_ms`，原 `timings.total_ms` 包含上下文处理耗时。

前 **6 轮** 沿用原有追问处理：用最近问题、独立检索问题和有长度限制的回答消解指代。第 **7 轮起**，同一次追问改写调用还会维护结构化会话摘要，并使用摘要与最近 6 轮原始用户问题构建独立问题。摘要保留查询对象、时间、版本、过滤/排除/例外条件及未经核实的用户业务陈述；每项必须有原始用户问题的轮次和连续引文。摘要阶段不输入助手回答或历史改写结果，避免把模型输出变成用户事实。第一轮不调用改写模型，不增加单独的摘要调用。

完整问答和引用保存在 PostgreSQL；结构化摘要写入 `conversations.memory_json`，最多 16 项、4 组未决冲突、8 KiB。窗口外的原始历史不会直接进入模型，但已提取的早期条件可以通过摘要保留。明确更正保留来源并替换旧条件；同一对象和场景的不同值未经更正会标记冲突，本题相关时先澄清；不同对象、时期、版本或场景分别记录。历史金额/业务数值不能被带入当前问题作为计算前提，需用户本轮重新写明或由检索资料提供。摘要和历史均**不是答案证据**，每轮仍执行现有授权检索和最终校验。

`CONVERSATION_MEMORY_ENABLED=false` 默认关闭实验摘要，使用 6 轮窗口的改写方式；显式设为 `true` 后启用结构化摘要。隐私截止点仍生效。旧长会话只从当前可读窗口建立摘要，并记录覆盖起点，不补读全部历史。摘要不保证无损保存或识别所有自然语言冲突，不包含长期用户画像或跨会话记忆。详见 [会话摘要与冲突管理说明](docs/CONVERSATION_MEMORY.md)。

**摘要目前仍属实验功能**：[真实模型 42 轮验证](docs/CONVERSATION_MEMORY_LIVE_20261014.md) 中三组完整场景均未通过，主要问题是结构化提案适配与失败恢复。2026 年 10 月 28 日合并时将代码默认值与示例配置改为关闭，保留原失败记录；显式环境配置仍优先，不放宽最终校验。

同一对话的并发写入返回 `409`；超时租约可自动恢复，旧请求不能覆盖新历史。问答轮次、摘要更新与成功审计一起提交，失败不留下半轮或新摘要。权限依赖由服务端合并，观察到撤权时清空摘要并持久化历史截止点，重新授权后也不会沿用该截止点之前的上下文。此隐私重置独立于问答成功，失败请求也不能撤销已经观察到的权限变化。模型调用前后和结果提交前重新检查用户与固定知识库范围的权限。

历史内容属于敏感业务数据：审计只记录动作与统计，不包含问题、回答和正文；部署时仍需配置数据库访问控制、备份加密和历史保留策略。页面只展示最近 20 段对话、恢复最近 100 轮，更早记录可以通过分页接口读取。浏览器超时后请先恢复该对话确认是否已完成，再决定重试（目前没有请求幂等去重）。

## 默认跨授权知识库问答

登录后默认选中“全部可访问知识库”，无需先选部门即可提问。左侧的具体知识库是可选筛选；文档上传、删除和索引任务仍必须选择一个具体知识库。

`POST /api/qa` 的 `knowledge_base_id` 可省略或传 `null`，后端根据登录身份重新计算同租户的公共知识库、部门授权和用户直接授权；指定 ID 时维持原单库模式。客户端不能自行提供授权知识库列表。没有任何可读知识库时返回 `403`，不会退化成无过滤搜索。

```json
{"question": "开放平台令牌过期后怎么处理？"}
```

多轮全范围模式使用 `POST/GET /api/conversations` 和 `GET/DELETE /api/conversations/{conversation_id}`，分页参数与单库接口一致。创建后将 `conversation_id` 传给问答接口，保持 `knowledge_base_id` 为空。原 `/api/knowledge-bases/{knowledge_base_id}/conversations` 接口及已有历史继续有效，两种模式的会话 ID 不能混用。

向量检索和 BM25 都先按 `tenant_id + 授权知识库 ID 集合 + ready 文档` 过滤，再取候选；跨库共享 30 条融合候选及默认 12 条重排子块结果预算，不会按知识库数量增加完整问答调用。每条引用携带 `knowledge_base_id` 和 `knowledge_base_name`，用于展示来源所属库；父块展开还需 SQL 复核权限与版本。

每轮历史保存当时的完整授权检索范围。后续权限缩小时，可能受影响的旧轮次及标题会被隐藏，追问改写只使用最后一个失效轮次之后的安全历史。此策略是保守的：即使旧回答只引用了其他库，只要该轮曾允许检索的某个库不再可访问，也会隐藏该轮；保存的数据库原始历史不会被删除。新增授权不会把历史对话变成单库对话，旧单库对话也不会自动扩大范围。

新增迁移 `b8d0f2a4c607`：会话知识库字段允许为空，并添加轮次授权范围快照和跨库会话的租户外键。启用前停止 API/Worker 写入、执行 `python -m alembic upgrade head`，再重启服务并刷新页面。若数据库仍未完成前面的 BM25 迁移，请先按下面“已有环境升级”准备 pg_search 镜像，不要跳过迁移或手工修改 Alembic 版本号。回滚发现全范围会话时会拒绝执行，要求先导出并处理，避免静默丢失记录。

## PostgreSQL 持久化 BM25

正式问答链路使用 `pg_search 0.25.11` 的 BM25 索引，不再每次读取整个知识库并在 Python 内存中重建关键词索引。检索流程仍为：向量候选 30 条 + BM25 候选 30 条 → 加权 RRF（0.6 / 0.4）取 30 条 → Qwen 重排取 8 条；重排失败时保留原 RRF 降级行为。

- 新增 `document_chunks.keyword_text`，保存 jieba 搜索分词后的文本；正文、引用和向量字段不变。
- 入库、更新、历史回填、查询共用 `jieba-v1` 分词规则，保留中文词、数字、错误码和接口标识符；扩展使用 whitespace tokenizer，避免再次按英文规则切词。
- 关键词查询绑定参数，并在同一条 SQL 中限制租户、知识库和 `ready` 文档，再排序取前 N 条；不会先取全局候选再做权限过滤。
- BM25 索引由数据库持久化，分块的插入、更新、删除和回滚随数据库事务生效；不再维护独立内存缓存。
- API 和 Worker 启动时检查扩展版本、预加载配置和索引状态。缺少扩展时明确报错，不会偷偷回退到全库扫描。Python BM25 工厂只保留给小型离线评估和单元测试。

数据库镜像基于原来的 PostgreSQL 16 / Debian Bookworm / pgvector 0.8.6，额外安装固定版本、校验 SHA256 的 pg_search 安装包。该镜像里的 vector 扩展是 pg_search 的依赖，也供历史迁移使用；当前业务不再保存 SQL embedding 列或使用 pgvector 向量查询。支持 amd64 / arm64，保持原数据库主版本、数据目录和 Compose 数据卷不变。已有数据卷不会重新运行初始化脚本，所以 Compose 显式设置 `shared_preload_libraries=pg_search`。

### 已有环境升级

先备份数据库并确认备份可恢复，安排维护窗口，等待正在执行的索引和问答请求结束。以下步骤会重启数据库，并暂停 API/Worker；**不要删除数据卷，也不要更换 PostgreSQL 主版本**。

在已安装新分支依赖的虚拟环境中执行（本机 `.env` 的 `DATABASE_URL` 应指向要升级的数据库）：

```powershell
docker compose build postgres api worker
docker compose stop api worker
docker compose up -d --no-deps postgres
docker compose ps postgres
# 确认数据库 healthy 后，再执行迁移。
python -m alembic upgrade head
docker compose up -d api worker
```

迁移会为现有分块按每批 500 条回填关键词文本，再建立索引；**无需重新上传文档，也不会重新调用 Embedding**。批处理控制的是内存，不是分批提交：整个迁移使用一个事务，DDL 和索引构建可能持锁，数据量大时应先在数据库副本演练耗时和磁盘需求。该迁移需要 Python 读取正文，不能使用 `alembic --sql` 离线生成回填。

若迁移失败，不要启动新版本 API/Worker；先排查日志和数据库资源再重试。降级迁移只移除 BM25 索引和 `keyword_text`，保留正文、向量及共享的 pg_search 扩展；代码也必须同步回到旧分支，不能让新代码连接降级后的表结构。

### 检索质量与验证

旧内存 BM25 的统计范围是当前知识库，新索引的词频统计范围是整张分块表，因此排序不保证与旧实现完全一致；授权过滤仍然只返回指定租户和知识库内的有效分块。合并前应使用现有检索测试集复测召回和排序质量。本次不调整融合权重和重排参数，也不引入 Elasticsearch。

修改分词规则或 jieba 词典时，需要新的分词版本并重新回填/重建关键词索引，不能仅改查询端。上线前还应核对所选 pg_search 版本的许可和部署要求。

独立集成测试需先在专用测试数据库执行迁移，数据库名称必须以 `_test` 结尾；不要指向业务数据库：

```powershell
$env:BM25_TEST_DATABASE_URL = 'postgresql+psycopg://test_user:test_password@127.0.0.1:55433/enterprise_bm25_test'
$env:RUN_BM25_INTEGRATION_TESTS = '1'
python -m pytest tests/test_pg_bm25_integration.py -q
```

测试覆盖真实扩展的中文、数字/错误码匹配，租户与知识库过滤先于 LIMIT，非 ready 文档排除，以及分块更新、删除和回滚。CI 使用同一个数据库 Dockerfile 构建镜像，并运行 BM25 和 Milvus 集成测试。

实现采用固定版本的 [pg_search v0.25.11](https://github.com/paradedb/paradedb/tree/v0.25.11)，索引与查询 API 参考该版本的 [create-index](https://github.com/paradedb/paradedb/blob/v0.25.11/docs/documentation/indexing/create-index.mdx)、[whitespace](https://github.com/paradedb/paradedb/blob/v0.25.11/docs/documentation/tokenizers/available-tokenizers/whitespace.mdx) 和 [match](https://github.com/paradedb/paradedb/blob/v0.25.11/docs/documentation/full-text/match.mdx) 文档。

## 业务版本选择与受限计算

正式问答链已增加业务有效期元数据、候选版本选择与来源绑定的时间/金额计算校验。单轮和多轮共用这些能力，保留原有向量、PostgreSQL BM25、RRF 和重排流程，不新增业务表，也不要求旧文档全量重建向量。实现、限制与启用方法见 [RAG 可靠性说明](docs/RAG_RELIABILITY.md)。

## 当前项目边界

项目目前适合本地开发、功能演示和技术方案验证，距离生产环境还需要继续补充：

- 将当前数据库任务队列扩展为可水平扩容的多 Worker 队列
- 生产级对象存储和文件病毒扫描
- Keycloak Realm 自动化配置和密钥管理
- OpenTelemetry 分布式追踪和跨服务 Trace 上下文
- 上传频率控制和可视化审计管理后台
- 数据库备份、恢复演练和滚动迁移策略
- 大规模文档下的 BM25 参数调优、查询计划验证和多节点容量测试
- 长期用户记忆、历史保留策略、知识库管理后台与更多交互功能

## 开发流程

建议每个阶段使用独立分支：

```powershell
git switch main
git pull origin main
git switch -c codex/<feature-name>
```

提交 Pull Request 前至少执行：

```powershell
python -m compileall backend scripts
python -m pytest -q
git diff --check
```

## 文档清洗与切片增强

清洗、条件关联、片段用途、OCR 待复核与旧索引兼容说明见 [文档清洗与切片](docs/DOCUMENT_CHUNKING.md)。本地只读检查可使用 `scripts/inspect_document_chunks.py`，不会生成向量或修改数据库。

最新回归优化说明见 [历史追问、答案完整性、OCR校对与评分](docs/RAG_REGRESSION_OPTIMIZATION.md)，包括无需全量重新入库的边界，以及按题号/会话组定向复测方法。

合成语料声明与业务正文的分离原则、备份和更新校验说明见 [业务语料来源治理](docs/CORPUS_PROVENANCE.md)。该操作只处理星海测试文档，不会在通用解析器中删除法律或测试相关词语。

## 模型 Token 与成本审计

问答、追问改写、查询/文档向量化和重排的实际调用用量可独立保存在 PostgreSQL，并接入管理员接口、分层评估报告与 Grafana。未知用量不记为零，价格可配置且保留调用时快照。新增迁移后无需重新入库。配置、权限与准确性边界见 [模型用量审计](docs/MODEL_USAGE_AUDIT.md)。

## 小规模知识库扩容语料

新增的[星海澄川扩容包](sample_docs/xinghai_expansion/README.md)包含20份分部门Markdown文档和
[统一80题评估题集](evals/archive/xinghai_v3/README.md)中的20道新增题及证据标注，覆盖历史版本、相似故障、实际验收与跨库关联。
生成过程仅使用本地Python；原语料与题集保留。只入库`documents`中的业务文件，评估答案不得入库。

原30份合成基准原文及其清单已纳入`sample_docs/xinghai_base`，克隆后无需本机历史输出。
运行`python scripts/merge_xinghai_evaluation.py --prepare-corpus`即可准备50份统一评估原文；CI执行相同准备与校验。

## License

当前仓库尚未声明开源许可证。如需公开复用或接受外部贡献，请先增加明确的 `LICENSE` 文件。

### 请求可靠性补充

嵌入请求默认超时 30 秒，可通过 `EMBEDDING_TIMEOUT_SECONDS` 在 0 至 120 秒内配置（不含 0）。问答执行期间自动续期 Redis 并发租约；续期失败或租约丢失时返回 503，避免将失去并发保护的请求记为成功。知识库列表与全范围检索通过固定次数查询汇总当前公共、部门及个人授权，仍在每次请求重新鉴权。

引用预览接口为 `GET /api/knowledge-bases/{knowledge_base_id}/documents/{document_id}/chunks/{chunk_id}`；带 `parent_id` 时复核当前子父映射并返回父块全文，否则返回子块原文。每次预览重新鉴权，只读取当前就绪版本，响应禁止缓存。

同年日期省略年份及季度对比现在保留各期证据；跨年或无法确定年份时不猜测。2026-10-13的实现、真实80题基线、4题修复复测及剩余问题见 [可靠性验收记录](docs/RELIABILITY_ACCEPTANCE_20261013.md)。
