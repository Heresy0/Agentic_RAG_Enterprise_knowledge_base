# 制造业150题评估包

| 集合 | 题数 | 组成 |
| --- | ---: | --- |
| [开发集](development/single_turn.jsonl) | 50 | 原20题原样保留 + 30道新增公开资料题 |
| [冻结验收集](test/single_turn.jsonl) | 100 | 75道可回答题 + 15道拒答题 + 10道澄清题 |

[annotations.json](annotations.json)记录概念标识、来源族、英文锚点和PDF物理页码。[splits.json](splits.json)锁定语料与题集指纹。新增105道正例均从锁定原文编写，不是模型生成的实测回答；25道负例覆盖证据不足和必要条件缺失。

资料按文档分集，同一e-Handbook按整章分集，原20道已见题只用于开发。相关概念仍可能在不同技术资料出现，因此不称为完全独立的主题盲测。题目文字、ID和概念标识均无重复；不同句式并不自动等于不同业务能力。

当前仅完成[离线解析、来源锚点及PDF证据页复核](reports/20261028-offline-r1/README.md)，未运行本版150题真实问答。`answerable=false`不能区分拒答与澄清，需依据`expected_behavior`、`missing_information`和参考行为做语义复核。词面代理评分不作独立准确率。

只把语料包`documents/`内文件入库。本目录所有问题、答案、标签和报告都必须留在评估侧。实际入库与分批执行命令见[使用说明](../../docs/MANUFACTURING_PUBLIC_V2.md)。
