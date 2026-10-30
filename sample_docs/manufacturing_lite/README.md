# 制造业小规模演示语料 v1

共 17 份文件：PDF 5、原生 DOCX 4、Markdown 4、TXT 4。按公共参考（4 份）、生产工艺（8 份）、质量检验（5 份）分组。模拟企业为“衡川精密”，产品 HC-A17、工序 OP-20；所有企业、产品、事件和工艺数值均为合成设定，不可作为实际生产要求。

`documents/` 中 16 份为本项目编写的合成业务资料，1 份为 NIST 公开原文。上传时只选各知识库对应子目录中的文件；不要上传本说明、manifest、validation 或 evals 中的标准答案。

| 格式 | 用于验证的复杂内容 |
| --- | --- |
| PDF | 型号、单位、误差与备注的整行对应；四项跨页交接条件；24 行跨页长表与重复表头；纯图片扫描件；160 页英文公开手册 |
| DOCX | 原生标题、列表与检验表；相同文件编号的 1.0/2.0 业务版本；生效和失效日期；在制工单例外 |
| Markdown | 多级章节、混合表格、转义竖线、代码围栏中的伪标题和竖线 |
| TXT | 工单交接、实际开工与报工日期差异、缺少完成记录、量具隔离记录及 UTF-8 BOM |

这些复杂结构不等于覆盖所有工业脏文档。当前不覆盖手写记录、CAD 图纸、完整修订痕迹或任意合并单元格。GBK 输入会明确报编码错误，应先保留原件并转换编码，不能宣称已支持任意编码。

## 来源与使用边界

公开原文为 Theodore Doiron、John Beers 的 *The Gage Block Handbook*，NIST Monograph 180（1995）。本仓库保存从 [NIST 官方 PDF](https://nvlpubs.nist.gov/nistpubs/Legacy/MONO/nistmonograph180.pdf) 获取的完整 160 页版本；[出版信息](https://www.nist.gov/publications/gage-block-handbook-0)。原始字节未改写，来源、取得时间、大小和 SHA-256 见 `manifest.json`。

按 [NIST 图书馆 FAQ](https://www.nist.gov/nist-research-library/library-faqs) 和 [版权说明](https://www.nist.gov/copyrights-disclaimers)，NIST 联邦出版物通常在美国属于公有领域，另有版权标识的第三方内容除外；使用时保留作者及来源。这里将它用于计量常识参考，不将旧版手册当作现行标准或企业批准文件，也不暗示 NIST 对本项目背书。

NIST 原文包含老式排版、图表和低文本页，需 OCR 运行环境；某些公式的阅读顺序仍不可靠。公开题的证据采用物理第 13 页已核对的原文，不能从一条证据通过推断整本手册已无损解析。

## 文件与标注隔离

- `manifest.json`：文件清单、来源、版本与字节哈希；不是新增业务数据库表。
- `validation/scan_ground_truth.json`：对照原图维护的独立标注，只用于验证，绝不作为 OCR 提取器或检索正文。
- [独立题集](../../evals/manufacturing_lite/README.md)：20 道题，含 4 道资料不足拒答题。
- 扫描件是真正的图片型 PDF，没有隐藏文本层。原始真实 OCR 命中 3/6 个已标注关键字段；对照原图后的可信定点校对命中 6/6，整页仍标记待复核。校对绑定原件哈希、页码及唯一短语，OCR 引擎或原件变化导致不匹配时须重新检查。

## 重建与验证

普通运行只需要提交的成品文件。重建需要 Python 的 `python-docx`、`reportlab`、`pdf2image` 及 Poppler；使用具备这些依赖的解释器，在仓库根目录执行，选择空输出目录：

```powershell
python scripts/build_manufacturing_corpus.py --output-dir tmp/mfg-rebuild --dataset-dir tmp/mfg-evals --reference-file "sample_docs/manufacturing_lite/documents/公共参考/05_NIST量块手册公开参考.pdf"
```

也可显式使用 `--download-public` 从已登记的 NIST 官方地址下载。两种输入都须匹配本版已核验的原件哈希，避免将其他 PDF 误标为 NIST 资料。脚本拒绝覆盖非空目录，不访问业务库或模型。重建的 PDF/DOCX 字节可能随工具版本变化，需重新检查哈希和扫描件校对记录，不能直接沿用旧原件的复核。

现有语料的验证命令、演示租户及入库步骤见 [实施与验收说明](../../docs/MANUFACTURING_LITE.md)。
