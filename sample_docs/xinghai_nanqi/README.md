# 南栖项目知识库扩容包

公司、项目、人员、事件、规范与数值均为合成设定，用于本项目功能回归；不是实际企业数据或独立盲测。
本包新增12份Markdown业务文档，四个现有知识库各3份，编号57至68，仅适用于NQ-2026-09。
原80题、50份主语料与6份多跳增量语料保持原样；南栖专用规则不替换其他项目规则。

只将documents中对应部门的业务正文上传至同名知识库。README、manifest、入库清单、题目、答案和证据标注不入库。
公共资料仅保留范围及路由，不公开HR任务明细和平台运行时限。跨部门引用不改变用户授权。

事实源为scripts/corpus_100_data.py；生成器不请求模型、不连接数据库。声明集中在此说明和manifest，业务正文不混入测试答案或系统指令。

```powershell
python scripts/build_100_evaluation.py --prepare-corpus
python scripts/build_100_evaluation.py --check
```

新增20题位于evals/xinghai_nanqi；合并100题位于evals/xinghai_v4。
真实上传通过现有OIDC与editor权限保护的API，索引会使用当前配置的Embedding服务并产生费用。
本地生成和解析通过不能证明已经入库、在线召回或模型回答正确；入库结果另存本地运行收据。
