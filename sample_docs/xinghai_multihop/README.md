# 六份复杂与多跳语料增量

公司、项目、人员、事件、规范与数值均为合成设定，仅用于本项目功能验证，不代表真实企业管理规则。
声明集中在本说明和manifest，业务正文不包含测试题、参考答案或模型指令。

| 知识库归口 | 文档 | 数量 |
| --- | --- | --- |
| 技术部 | 51项目范围登记、53校验与签署记录 | 2 |
| 平台研发部 | 52验收接口规则、54配置映射、55服务等级、56事件记录 | 4 |

青屿场景通过项目→规则/证据包→实测/签署构造验收问题；雾岑场景通过事件→配置→服务等级→约定构造响应判断。
明确区分旧配置与现行配置、计划与实际、首次告警与人工发现、临时恢复与永久整改。
新对象使用独立编号，不新增旧80题负例所问的海岚/澄川事实，不修改原规则或原题。

只将`documents/`下的六份正文按上述部门入库。**不上传本README、manifest、生成脚本、题册、答案和证据链。**
文档互相引用不授予跨库权限，继续使用现有授权。此次生成与静态验证不连接数据库、不调用嵌入/问答模型；
在内容确认后再执行入库与真实模型评估，不能把静态证据检查称为问答通过。

生成器从六份受版本管理的Markdown源文件准备manifest、题目和人读题册，不复制参考答案进正文：

```powershell
.venv\Scripts\python.exe -X utf8 scripts/build_multihop_expansion.py
.venv\Scripts\python.exe -X utf8 scripts/build_multihop_expansion.py --check
.venv\Scripts\python.exe -X utf8 scripts/build_multihop_expansion.py --prepare-corpus
```

最后一个命令准备Git忽略的`output/knowledge-corpus-multihop/`，包含原50份与本6份原文，
不会改写原`output/knowledge-corpus-unified/`、原80题或历史报告，也不执行入库。
六份正文直接入库时采用现有浏览器文档上传入口，分别选择技术部和平台研发部知识库。
题集和完整运行命令见`evals/xinghai_multihop/README.md`。
