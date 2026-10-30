# PDF明确缺陷修复：首轮回归

本轮仅修改通用PDF提取与切分规则，并阻断空OCR结果的静默入库。未改原件、题目、参考答案或逐字证据，未更换OCR引擎或调整DPI/语言，未连接业务库、Embedding、重排及生成模型。

## 修复内容

1. 分栏判断增加真实横向留白检查。长行与短行的单词中心有间隔，不再足以证明页面有两栏。
2. 同行文字按垂直重叠关系归并，修复下标被挪到下一行的问题。
3. 完整正文句子不再因为以数量开头就被识别为标题，例如“3 次，取平均值……”。含逗号或多句正文的跨行文字优先保留为正文，字号标记不会覆盖这一保护；内部排版标记不进入分块。小数章节编号不再误作单层列表，避免前半句与末尾文字分离。短标题与正常编号列表仍有回归覆盖。
4. 需要OCR但返回空文字的页保留在观察记录中，抛出带物理页码的`PdfTextExtractionError`，阻断整份资料入库。无法确认的图片页不被直接认定为空白；无图片的原生空白页仍可正常跳过。

解析版本由`structured-v5`升至`structured-v6`。旧索引未重建；只有以后显式索引时才使用新版本。没有针对文件名、页码、题目或答案的特殊处理，也未回填OCR文字。

## 同一冻结题集的对照

| 检查 | 修复前 | 修复后 |
| --- | --- | --- |
| 严格证据匹配 | 106/150 | 120/150 |
| 未匹配证据 | 44 | 30 |
| 全部锚点匹配的正例 | 57/90 | 70/90 |
| 改善/退化的证据 | — | 14/0 |
| 非空分块 | 1,290 | 1,034 |
| 解析结果 | 18份非空 | {'passed': 17, 'blocked': 1} |

工业内窥镜原件MFG-ZH11现在明确阻断，16个空OCR页为2–13、15–18。修复前仅留下2页非空文字、生成1块；修复后不发布残缺分块。这属于失败检测修复，不是OCR识别能力恢复。

当前准备度：{'sampled_evidence_verified': 12, 'blocked_body_missing': 1, 'review_required_evidence_mismatch': 5}。剩余差异首次观察阶段：{'ocr_text_difference_or_loss': 30}。全量问答准备度仍为`false`。尚有20道正例存在未匹配证据；零退化只表示这些已冻结锚点没有新退化，不等于全文无错误。

这些统计不代表问答准确率或检索召回率。现有80题未用于问答调参，但其中页面已经用于解析诊断，不能称为完全未接触的盲测。

## 验证与保留

- 16项新增回归覆盖误分栏、真实双栏、下标、数量开头正文、跨行条款与字号标记、列表续行、空OCR阻断、原生空白页、阻断缓存，以及3个真实缺陷页；原生双栏和表格旧测试继续通过。
- 专项98项通过；全量1,421项通过、55项跳过、0失败/错误。跳过项不算通过。
- 149个原有基线文件和37个本轮之前已提交的中文v3原文/评测/报告文件字节保持不变。
- 实际18份重跑采用已有镜像`enterprise-knowledge-manufacturing:20261028-fe7221c`，禁网、只读代码/原文挂载，仅新的临时观察目录可写。没有覆盖首轮报告和缓存。
- 新解析器SHA-256：`fc199760920294077744c5ee0b1219f3143e8e8467bdd2c39d69b72e1b3719cc`；题集冻结SHA-256：`8ec785ae0a4513df83a8278211d135bd8c7fb29d70d67f27ccdadd1dceca1c9e`。

## 结果文件

- [严格证据对照及逐项改善/退化](comparison.json)
- [实际解析与阻断页码](parsing.json)
- [分块审计](chunk-audit.json)
- [逐条证据检查](labels.json)
- [准备度与剩余缺陷](readiness.json)
- [基线保留证明](baseline-preservation.json)
- [测试结果](validation.json)
- [修复前原始报告](../20261029-offline-r1/README.md)

## 复现与后续

按照[中文版本说明](../../../../docs/MANUFACTURING_ZH_V3.md)的隔离Docker命令，使用新的临时目录`tmp/manufacturing-zh-fix-r2`挂载到`/validation-output`。源文件和脚本挂载保持只读，禁止复用修复前的解析缓存。实际解析命令返回3，表示有明确阻断的资料。

```powershell
python scripts/validate_manufacturing_zh.py --labels evals/manufacturing_zh_v3 --parse-cache tmp/manufacturing-zh-fix-r2/parsed --report tmp/manufacturing-zh-fix-r2/labels-recheck.json
python scripts/validate_manufacturing_zh.py --labels evals/manufacturing_zh_v3 --parse-cache tmp/manufacturing-zh-fix-r2/parsed --readiness-from tmp/manufacturing-zh-fix-r2/labels-recheck.json --report tmp/manufacturing-zh-fix-r2/readiness-recheck.json
```

证据命令仍会非零退出，因为剩余差异真实存在。下一轮再单独评估困难扫描件OCR和表格恢复，不以堆叠特殊规则追求现有锚点满分。
