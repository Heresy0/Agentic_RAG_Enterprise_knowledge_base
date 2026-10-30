# 制造业公开资料扩容包

本包60份上传文件：20份NIST/NBS原始PDF、24条NIST官方技术网页的Markdown转换版、16份保留的合成业务资料。网页条目来自同一本e-Handbook，不按24本书计数；原有量块手册未重复计数。物理格式为PDF 24、DOCX 4、Markdown 28、TXT 4。

只上传`documents/`。`originals/`保留24份网页HTML用于溯源，其他说明、清单、评估题与答案均不入库。

- [实施、重建与使用步骤](../../docs/MANUFACTURING_PUBLIC_V2.md)
- [逐文件SHA及知识库分组](manifest.json)
- [公开来源锁](../manufacturing_public_v2.sources.json)
- [来源及复用说明](NOTICE.md)
- [50道开发题与100道冻结验收题](../../evals/manufacturing_public_v2/README.md)

公开资料主要为英文历史技术参考；企业业务原文为明确标注来源的合成资料。没有真实企业内部数据和实时ERP/MES数据。本包已在隔离租户全部入库并完成50题开发集实测，100题冻结验收集未运行；[首轮结果](../../evals/manufacturing_public_v2/reports/20261028-development-live-r1/README.md)包含失败和不完整快照，不是150题验收通过率。
