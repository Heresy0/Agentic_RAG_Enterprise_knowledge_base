# 制造业首期工单语料与夹具

本目录是首期 M0 的离线资料配置，版本为 `manufacturing-workorder-v1-r1`。13 份资料全部属于合成业务设定：10 份通过仓库相对路径和原始字节哈希引用既有原件，3 份为新增的具体工单指示。既有原件没有复制、改写或重新索引。

| 文件 | 内容与用途 |
| --- | --- |
| [manifest.json](manifest.json) | 13 份原件的仓库相对路径、哈希、文档角色与待审核适用性标注 |
| [work_orders.json](work_orders.json) | 独立模拟租户下的 8 张工单，不含答案或预期适用版本 |
| [rules.json](rules.json) | 5 条与原文摘录绑定的选版规则，全部为 `draft` |
| `documents/生产工艺/` | WO-019 的单工单切换指示，以及 WO-024 两份未声明替代关系的冲突指示 |

工单列表为 WO-017～WO-024。WO-017 在 10 月实际开工；WO-018 在 9 月实际开工但于 10 月报工；WO-019 有明确书面例外；WO-020 缺少实际开工日期；WO-021 使用未收录指导书的另一产品；WO-022 属于另一工厂；WO-023 用于后续修订变化测试；WO-024 具有冲突依据。

`tenant_id` 是本包固定的合成夹具身份，不代表本机已有业务租户。M1 接入阶段再通过独立初始化工具登记源系统和授权。记录中的 `source_updated_at` 是源修订时间；真正查询时由平台另行生成 `fetched_at`，不能把夹具日期当作实际查询时间。

文档中的签发、通知和生产事件都是模拟设定。离线核对通过不等于文档已进入运行库、人工审核已完成或允许实际生产使用。规则中的对象和版本只来自业务原文，运行服务不得导入评测标准答案。

从仓库根目录执行只读检查：

```powershell
.venv/Scripts/python.exe scripts/manufacturing_phase1_workflow.py check
```

配置生成入口为 `scripts/build_manufacturing_workorder_profile.py`。它先预检整批目标文件，只创建缺失文件；发现已有内容不同则拒绝，不覆盖旧版本。JSON、题集及新增原件以固定 UTF-8 和 LF 写入，并由 `.gitattributes` 保留原字节。

后续摄入只遍历清单登记的原件。不要递归上传本目录：README、工单夹具、规则标注、schema 和评测标准不属于知识正文。旧 16 份公开资料与 98 题环境继续使用原清单。

详细范围与验收见[首期方案](../../docs/MANUFACTURING_PHASE1_PLAN.md)，题集见[独立回归配置](../../evals/manufacturing_workorder_v1/README.md)。
