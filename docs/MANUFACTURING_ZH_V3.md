# 制造业中文公开原文 v3

首轮完成中文资料收集、真实解析与切分检查，以及独立于现有基线的中文评测集；当时没有入库或运行应用模型问答。

现已完成[中文40题开发集真实HTTP首轮](../evals/manufacturing_zh_v3/reports/20261029-development-live-r1/README.md)：以`structured-v7`／`conservative-v2`在新租户导入18份原文，17份就绪、1份空OCR阻断。自动30/40，助手非独立复核33/40；普通文档题27/30、拒答6/6、澄清0/4。冻结开发集没有OCR专项题，相关准确率为null；OCR入库子块锚点仍为25/55。原215个基线文件和旧租户资料保留，80题测试集未执行模型问答。以下旧表格与离线过程保留原轮次口径。

后续已完成[PDF明确缺陷首轮修复](../evals/manufacturing_zh_v3/reports/20261029-parser-fix-r1/README.md)，解析版本为`structured-v6`。下表与原始离线报告保留首轮`structured-v5`观察；最新对照、阻断页码与剩余差异见修复报告。原文、题目及参考答案未更改。

## 交付与当前状态

当前代码已增加[统一文档清洗](DOCUMENT_CLEANING.md)，版本为 `structured-v7` / `conservative-v2`；改造前后的 18 份中文资料分块正文和原有结构元数据一致。以下数据仍为原轮次观察，清洗重构没有重新运行 OCR 或问答。

| 项目 | 结果 |
| --- | --- |
| 中文公开原文 | 18份，16份原始PDF与2份官方网页正文转Markdown |
| PDF规模 | 286个物理页；6份扫描/拍照文档 |
| 业务覆盖 | 生产质量责任、进货与过程风险、质量追溯、试验设备与量具校准 |
| 项目实际解析 | `structured-v5`，18份均产生非空分块，共1,290块 |
| 真实中文OCR | 6份文档产生182个OCR分块，全部带待复核标记 |
| 中文题集 | 120题：开发40、测试80；90题可依据原文作答、18题拒答、12题澄清 |
| 原文核对 | 44个完整PDF页面及两份官方HTML正文；150处逐字证据定位 |
| 严格证据检查 | 106/150处在指定物理页范围的分块中匹配，44处未连续匹配；57/90道正例的全部锚点匹配 |
| 全量问答验收准备 | 未通过：1份正文严重缺失，11份存在抽样证据差异，6份抽样证据全部匹配 |
| 原基线 | 149个既有语料、题集及报告文件SHA-256保持不变 |

结果是离线检查，不是问答准确率、检索召回率或独立专家认证。未连续匹配可能来自OCR、阅读顺序、单位/上下标或切分变化，不能把44处未匹配直接称为44道问答失败。

- [原文目录](../sample_docs/manufacturing_zh_v3/README.md)
- [来源锁及未收录候选](../sample_docs/manufacturing_zh_v3.sources.json)
- [评测集说明](../evals/manufacturing_zh_v3/README.md)
- [本轮检查报告](../evals/manufacturing_zh_v3/reports/20261029-offline-r1/README.md)

## 来源与版本

原始PDF保持下载字节，记录来源URL、官方转载页、下载时间、大小和SHA-256。HTML只提取经核实的正文节点，转为Markdown并保留原始HTML；同一网页的原件和转换件只计一份。题目、答案、来源锁、报告、页级观察和解析缓存不进入正文目录。

`publisher`表示公开提供机构；历史文件原发布机构、年份及规范编号以原件为准。2009/2014年的扫描规范作为锁定版本的技术参考与解析压力样本，未认证其全部最新适用性。公开可读没有被当成统一开放再分发许可，完整资料对外发行前另核复用条件。

优先候选中的认监委机械设备指南、上海激光测厚仪规范等因原件下载返回访问限制，未计入18份。案例征集通知也没有当作技术指南凑数；具体原因保留在来源锁。

原有17份四格式业务示例、20题回归和v2的60份资料、50/100分集及实测报告保留原样。新批次实际为PDF和Markdown两种上传格式，没有将转换产物冒充新的独立原文。

## 复现资料包

工作目录为仓库根目录。以下脚本只访问已审阅的官方HTTPS域名，校验重定向、文件类型和20MiB大小限制；不关闭TLS验证，不覆盖已存在的资料包和来源锁。

```powershell
python scripts/build_manufacturing_zh.py acquire --cache tmp/manufacturing-zh-v3/raw

# 已交付目录存在时，使用新的输出和锁文件路径复现。
python scripts/build_manufacturing_zh.py build `
  --cache tmp/manufacturing-zh-v3/raw `
  --output tmp/manufacturing-zh-v3/reproduced-corpus `
  --source-lock tmp/manufacturing-zh-v3/reproduced.sources.json
```

首次采集遇到网络失败会保留成功原件和失败记录，重试只复用指纹一致的缓存。冻结版本重新获取后，应先与已提交来源锁核对字节指纹；官网内容变化需建立新版本。

## 复现真实解析

使用本地已有镜像、禁网只读容器和真实Tesseract中文语言包。仅临时观察目录可写；既有业务数据和密钥目录不挂载。

```powershell
docker run --rm --pull never --network none --read-only `
  --tmpfs /tmp:rw,size=768m --cap-drop ALL --security-opt no-new-privileges `
  -e OMP_THREAD_LIMIT=1 -e PYTHONDONTWRITEBYTECODE=1 `
  --mount "type=bind,source=$((Get-Location).Path)\backend,target=/app/backend,readonly" `
  --mount "type=bind,source=$((Get-Location).Path)\scripts,target=/app/scripts,readonly" `
  --mount "type=bind,source=$((Get-Location).Path)\requirements.txt,target=/app/requirements.txt,readonly" `
  --mount "type=bind,source=$((Get-Location).Path)\sample_docs\manufacturing_zh_v3,target=/app/sample_docs/manufacturing_zh_v3,readonly" `
  --mount "type=bind,source=$((Get-Location).Path)\tmp\manufacturing-zh-v3,target=/validation-output" `
  enterprise-knowledge-manufacturing:20261028-fe7221c `
  python scripts/validate_manufacturing_zh.py --ocr --workers 2 `
    --parse-cache /validation-output/parsed --report /validation-output/parsing.json
```

缓存绑定原件、应用解析器源码、依赖清单、OCR审阅注册表及分块版本；命中缓存时是复核既有实际观察，不是新一轮OCR。首次缺少OCR时不把跳过算通过。`pages/`观察在去页眉页脚和切分前保存，没有用答案或人工转写替换OCR输入。

原始解析报告中的`passed`只表示有非空分块和结构字段。必须继续检查证据：

```powershell
python scripts/validate_manufacturing_zh.py `
  --labels evals/manufacturing_zh_v3 `
  --report tmp/manufacturing-zh-v3/labels-final.json

python scripts/validate_manufacturing_zh.py `
  --labels evals/manufacturing_zh_v3 `
  --readiness-from tmp/manufacturing-zh-v3/labels-final.json `
  --report tmp/manufacturing-zh-v3/readiness.json

python scripts/validate_manufacturing_zh.py --baselines `
  --report tmp/manufacturing-zh-v3/baseline-preservation.json
```

本轮标签命令存在真实未匹配锚点，预期非零退出；准备度命令也会阻断全量验收。错误没有通过缩短锚点、替换原件、修改OCR真值或删除困难题来消除。

## 中文题集的使用边界

每份原文5道独立问题。开发集使用6份资料、30道正例及10道行为题；测试集使用其余12份资料、60道正例及20道行为题。第75号令及辽宁实施指南归为同一来源族，保持在开发集。各规范主题有相关性，不宣称语义完全独立。

题集带原文物理页、多个事实锚点、原件指纹和参考答案；扫描页真值另绑定完整页渲染指纹及助手视觉复核记录。标注由同一助手编写核对，不是独立专家审阅。最终冻结前发现的一处“判断/判定”逐字标注错误已按原件纠正，修订记录随报告保存；此时尚未执行应用模型问答。

```powershell
python scripts/build_manufacturing_zh_questions.py `
  --visual-review evals/manufacturing_zh_v3/visual-review.json `
  --output tmp/manufacturing-zh-v3/reproduced-eval

pytest tests/test_manufacturing_zh.py -q
```

新JSONL沿用通用答案评估器的数据字段。资料收集阶段未接入HTTP验收；现有`run_manufacturing_http_acceptance.py`已支持中文冻结集、明确分集与请求上限，并在模型调用前核验来源、当前索引审计和所选题目的资料就绪情况。40题首轮已经完成，失败与评分边界见上方真实运行报告；测试80题保持未执行模型问答。修复解析不能直接算作真实问答已通过。
