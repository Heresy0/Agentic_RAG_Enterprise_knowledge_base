# 制造业公开资料扩容 v2

本版交付可复用的本地语料和冻结题集，原17份演示资料、20题及首轮真实HTTP结果保留。现已在隔离租户补齐43份新增文件，全部60份就绪，并完成50道开发题真实HTTP问答；100道冻结验收题尚未运行。[开发集首轮记录](../evals/manufacturing_public_v2/reports/20261028-development-live-r1/README.md)保留自动23/50、助手非独立复核32通过/13失败/1待确认/4快照不完整的结果。

## 实际规模

| 内容 | 数量 | 计数口径 |
| --- | ---: | --- |
| NIST/NBS原始PDF | 20 | 19份新增，原有量块手册只计一次 |
| NIST官方网页条目 | 24 | 同一本e-Handbook的独立技术条目，不称为24本手册 |
| 原有合成业务资料 | 16 | 保留企业版本、工单、质量异常及权限演示 |
| 上传文件合计 | 60 | 公共参考47、生产工艺8、质量检验5 |
| 物理文件格式 | 24 PDF、4 DOCX、28 MD、4 TXT | 不把格式转换或分块算作新增来源 |
| 开发题 | 50 | 原20题原样保留，新增30题 |
| 冻结验收题 | 100 | 75道公开资料可回答题、15道拒答题、10道澄清题 |

所有新增公开资料来自NIST官方域名。没有使用厂商资料来凑数；Mitutoyo、Renishaw、Sandvik候选资料因复用条件尚未核清而延期。一次未能获得的历史报告已在来源锁中列明，未计入成功数量。

主题覆盖量块比较、接触变形、长度溯源、圆柱与圆度、角度与分度台、光学平晶、测量不确定度、生产过程表征、SPC及接收抽样。原生DOCX/TXT继续由已有合成业务资料提供，没有把英文PDF机械转为四种格式。

## 来源、版本与边界

- 原文在[语料目录](../sample_docs/manufacturing_public_v2/README.md)，逐文件清单在`manifest.json`，44个公开来源的URL、获取时间、字节数、SHA256、年份及来源族在[来源锁](../sample_docs/manufacturing_public_v2.sources.json)。
- PDF使用NIST正式出版归档的字节版本。部分Toolbox镜像不可连接，归档版分页可能不同；题目中的页码是当前锁定文件的**物理页码，从1开始**。
- 24份网页保留原始HTML，转换版标明来源、转换日期与转换性质。保留正文、列表、数据表和图像链接，删除脚本与HTML注释；图像链接不等于识别了图像内容。
- 保留[NIST来源及复用说明](../sample_docs/manufacturing_public_v2/NOTICE.md)，不把NIST网站所有内容一概视为无条件可复制。历史报告的服务范围、溶剂、环境条件等不能直接成为工厂现行操作标准。
- 本批公开原文以英文为主，中文提问与中文参考答案可用于检验跨语言检索。不能称为60份真实企业内部文档，也不能声称已接入ERP/MES。

## 题目与数据隔离

开发集与验收集按来源文档分开，e-Handbook按整章分开：MPC章用于开发，PPC/PMC章用于验收。没有将同一网页换个问题措辞放进另一集合。不同公开文档仍可能讨论相近概念，因此这是文档级隔离，不代表完全独立的主题盲测。

`evals/manufacturing_public_v2/annotations.json`记录每题概念标识、来源族、原文锚点及PDF页码。`splits.json`锁定语料清单、来源锁、原20题、标注和两份JSONL的指纹。冻结集是可审计的公开标签，尚无模型调参使用记录；不是保密考试集或第三方独立认证。

新增105道正例均有中文参考答案、必需事实、英文证据及指定来源。另25道负例覆盖实时系统数据缺失、未提供的企业记录、未执行的写操作和条件不足。澄清题需要检查是否询问缺失条件；仅返回`answerable=false`不足以证明行为正确。允许给有依据的一般说明，但不得编造缺失的具体结果。

仅上传`sample_docs/manufacturing_public_v2/documents/`内的60个业务文件。`originals/`、清单、来源说明、问题、答案、标注、报告和解析缓存都不上传。分块缓存位于被Git忽略的`tmp/manufacturing-public-v2/parsed/`，不会进入向量库。

## 获取与重建

在已安装本项目依赖的Python环境执行：

```powershell
python scripts/build_manufacturing_public.py acquire
python scripts/build_manufacturing_public.py build --output tmp/manufacturing-public-v2/rebuilt-corpus
python scripts/build_manufacturing_questions.py --output tmp/manufacturing-public-v2/rebuilt-evals
```

获取只允许HTTPS NIST域名，逐次验证重定向，保持TLS校验，限制单文件20MiB。成功下载可复用缓存，失败会记录类型与HTTP状态，不会冒充已获取。构建必须与已审阅来源锁一致，拒绝覆盖非空输出目录；来源更新应建立新版本并重新审阅，不修改旧验收依据。

原始PDF和HTML已随本版本保留，日常运行无需再次下载。DOCX/PDF按二进制保存，清单和评估文件固定字节，避免Windows换行改变指纹。

## 离线验证

本轮[实际验证记录](../evals/manufacturing_public_v2/reports/20261028-offline-r1/README.md)包含真实解析和标签锚点结果。解析检查使用项目现有四种splitter和实际Tesseract OCR，在无网络容器中运行。`passed`表示有非空分块及必要结构元数据，**不表示整份文档语义、公式、表格和阅读顺序全部正确**。

OCR运行环境可使用本地已有制造业镜像。以下命令不调用Embedding、重排或生成模型；`docker run`的OCR和Python依赖取自现有镜像，代码取自只读挂载。

```powershell
docker run --rm --network none --read-only --tmpfs /tmp:rw,size=768m --cap-drop ALL --security-opt no-new-privileges `
  --mount "type=bind,source=$((Get-Location).Path)\backend,target=/app/backend,readonly" `
  --mount "type=bind,source=$((Get-Location).Path)\scripts,target=/app/scripts,readonly" `
  --mount "type=bind,source=$((Get-Location).Path)\requirements.txt,target=/app/requirements.txt,readonly" `
  --mount "type=bind,source=$((Get-Location).Path)\sample_docs\manufacturing_public_v2,target=/app/sample_docs/manufacturing_public_v2,readonly" `
  --mount "type=bind,source=$((Get-Location).Path)\tmp\manufacturing-public-v2,target=/validation-output" `
  enterprise-knowledge-manufacturing:20261028-fe7221c `
  python scripts/validate_manufacturing_public.py --ocr --workers 2 --parse-cache /validation-output/parsed --report /validation-output/parsing.json

python scripts/validate_manufacturing_public.py --labels evals/manufacturing_public_v2 --report tmp/manufacturing-public-v2/labels.json
```

缓存绑定原文件、解析器源码、OCR审阅注册表、依赖清单与分块版本；资料变动后相应缓存失效。省略`--ocr`且缓存不全时会跳过需要OCR环境核验的资料，并返回非零状态，不能把跳过算通过。

标签验证同时检查原文与实际分块。少量英文粘连允许只折叠空白进行锚点匹配，并明确记录匹配模式；不替换原始文档、不把参考答案喂给OCR、不据此推断检索召回率或问答准确率。

## 后续实际入库和验收

1. 已完成：正常HTTP接口新增43份，原17份SHA、文件ID及索引保留；60份共8,366子块和向量、4,828父块。新增嵌入697次物理调用，已知584,328 Token；未配置单价，不报告金额。
2. 已完成：50道开发题首次HTTP问答及结果复核。优先问题为最终上下文证据选择、父块预算、双栏混排及生成条件遗漏；原Q04仍遗漏书面切换例外，Q11仍遗漏归还条件。未重问以替换失败结果。
3. 待执行：先以开发集修复并保留本轮基线，配置固定后运行100题验收集。不得按失败题修改参考答案来美化结果；首次成绩与后续复测分开保存。

真实问答工具已支持明确选择集合、题号、数量上限，并在付费请求前拒绝缺少或未就绪的V2文件。运行配置需增加`corpus_manifest_sha256`，值取冻结文件。现有文档列表API只暴露文件名与状态，因此该前置检查不是数据库文件内容哈希证明；入库审计仍须保存上传端SHA与实际就绪记录。

下面是后续授权复测的小批量运行示例，会产生新的模型调用；请使用新的空输出目录。已创建的本地Keycloak演示令牌只有300秒有效期，可保留其refresh_token并选择提前续期：

```powershell
python scripts/run_manufacturing_http_acceptance.py `
  --dataset-dir evals/manufacturing_public_v2 --split development --limit 5 --max-questions 5 `
  --config <隔离环境配置.json> --tokens <本地私密令牌.json> `
  --output-dir <新的结果目录> --allow-model-calls --refresh-local-keycloak-token
```

完整验收选择`--split test --max-questions 100`。`--case-ids`可定向复测，不能选择另一集合的题号。所有运行保留问题集指纹、实际题号、调用次数、上下文快照和真实用量；未经观测的检索阶段仍标为未观测。关键词代理评分只作辅助，中文事实同义表达、引用支持和拒答/澄清应做语义复核。

`--max-questions`限制问答请求数，不限制每题内部模型调用次数或总Token费用。完整费用仍以实际用量账本为准。

续期仅用于本地演示Keycloak，使用已有refresh_token，不自动重试问答。长时间空闲后refresh_token也可能失效，需用原演示账号重新登录；不要把授权失败当成模型业务表现。上下文不完整仍由运行器严格拒绝作为完整快照；本轮4题只从原响应及账本恢复审计，未伪造完整上下文。
