# 制造业小改造：实施与验收

2026-10-28，本轮实现“制造业工艺知识与作业指导助手”的小规模版本：沿用现有知识问答链路，把资料、演示问题和回归场景换成机加工与尺寸检验。用户明确提供型号、工序和业务日期，系统根据已授权原文回答并给出引用。

## 本轮交付

| 内容 | 结果 |
| --- | --- |
| 四格式语料 | 17 份：16 份合成业务资料、1 份 NIST 公开原文；5 PDF、4 DOCX、4 MD、4 TXT |
| 业务问题 | 首件确认、换治具复检、工艺变更、在制工单例外、检验公差、量具校准与隔离、条件完整性及缺少记录的拒答 |
| 页面 | 欢迎页的三个示例问题及输入提示改为制造业内容，复用原登录、知识库与引用预览 |
| 解析修复 | PDF 编号完整句保留为正文，四项跨页条件不丢失；切片版本 `structured-v5` |
| OCR 校对 | 原图对照、文件哈希、页码与唯一误识别短语绑定，保留原始命中和待复核标记 |
| 授权 | 复用现有知识库权限；五角色离线验证；新增默认预演、只允许新企业的初始化脚本 |
| 回归 | 独立 20 题、专用解析验证器及现有统一评估入口兼容性验证 |

没有新增表、迁移、业务 API、MES/ERP 接口、微调或模型代理。型号、工序和工单目前是原文中的业务标识，没有新增结构化业务对象过滤；不能宣称已具备生产级自动选工艺能力。完整 [原实施方案](MANUFACTURING_IMPLEMENTATION_PLAN.md) 中的 MES、业务权限和额外元数据属于后续范围。

## 演示规则与版本

HC-A17 OP-20 的 1.0 版要求首件连续 2 件；2.0 版从 2026-10-01 新开工工单开始执行，要求连续 3 件，并保留质量签署等完整条件。此前已经开工的工单不因 10 月报工自动换版，除非存在针对该工单的书面切换通知。样例 WO-017 与 WO-018 专门展示这一差异。

沿用已有 `policy` JSON 元数据提取文档编号、版本、生效/失效日期和适用范围。不能仅按“上传时间最新”选版本；版本和例外须结合指导书、ECN 和工单记录。当前离线验证确认元数据及证据可提取，尚未证明生成模型能在全部题上正确判断。

文件与来源见 [语料说明](../sample_docs/manufacturing_lite/README.md)，问题与评估边界见 [题集说明](../evals/manufacturing_lite/README.md)。

## 演示租户与权限

公共参考设为 `company`，同租户有效用户自动获得 viewer；其余两库设为 `restricted`。角色采用既有个人授权，范围如下。

| 角色 | 公共参考 | 生产工艺 | 质量检验 |
| --- | --- | --- | --- |
| 操作员 | viewer | viewer | 无 |
| 工艺工程师 | viewer | editor | viewer |
| 质量工程师 | viewer | viewer | editor |
| 知识管理员 | admin | admin | admin |
| 仅参考资料用户 | viewer | 无 | 无 |

先在现有身份系统创建五个演示身份，将 `evals/manufacturing_lite/subjects.example.json` 复制到本地 `tmp/mfg-subjects.json`，填入各自真实 sub。脚本不会创建 Keycloak 账号，不写入令牌，也不会搬动已绑定到其他企业的用户。预演不连接数据库：

```powershell
.venv/Scripts/python.exe scripts/provision_manufacturing_demo.py --subjects tmp/mfg-subjects.json
```

确认目标环境和身份映射后，显式执行下述命令会在配置的 `DATABASE_URL` 中创建**一个全新的企业、三个库和五个用户映射**。同名企业或已有 sub 会使操作失败；本轮没有执行该命令。

```powershell
.venv/Scripts/python.exe scripts/provision_manufacturing_demo.py --subjects tmp/mfg-subjects.json --apply
```

用知识管理员登录工作台，将 `sample_docs/manufacturing_lite/documents/公共参考`、`生产工艺`、`质量检验` 中的文件分别上传到对应库，等待 ready。入库会调用 Embedding；本轮没有上传、建立向量索引或修改既有账号的授权。不要把含标准答案的验证目录一起递归上传。

随后使用操作员和仅参考资料用户登录，检查列表、直接文档访问、引用预览与撤权后的访问；这是后续真实 HTTP 验收步骤，不能用前端隐藏或离线单元测试替代。

## 本地验证

有完整 OCR 依赖的环境可以直接运行：

```powershell
.venv/Scripts/python.exe scripts/validate_manufacturing_corpus.py --ocr --report tmp/mfg-corpus.json
```

不带 `--ocr` 会跳过需要识别的两个 PDF，返回码 2 表示未完成；失败返回 1，全部已标注检查通过返回 0。检查包含哈希及目录一致性、真实切分器、完整参数行、跨页条件组、长表表头、代码围栏与真实 OCR。报告路径不能放进上传目录。

本机 Windows 没有 Tesseract；本轮使用已有 `enterprise_knowladge-api:latest` 镜像，在禁用网络的临时容器中验证。可复用下面的 PowerShell 命令（在仓库根目录，镜像须已构建）：

```powershell
$mfgRoot = (Get-Location).Path
New-Item -ItemType Directory -Force tmp/mfg-validation | Out-Null
docker run --rm --network none --read-only --tmpfs /tmp:rw,size=512m --cap-drop ALL --security-opt no-new-privileges `
  --mount "type=bind,source=$mfgRoot/backend,target=/app/backend,readonly" `
  --mount "type=bind,source=$mfgRoot/scripts,target=/app/scripts,readonly" `
  --mount "type=bind,source=$mfgRoot/sample_docs/manufacturing_lite,target=/app/sample_docs/manufacturing_lite,readonly" `
  --mount "type=bind,source=$mfgRoot/evals/manufacturing_lite,target=/app/evals/manufacturing_lite,readonly" `
  --mount "type=bind,source=$mfgRoot/tmp/mfg-validation,target=/validation-output" `
  enterprise_knowladge-api:latest python scripts/validate_manufacturing_corpus.py --ocr --report /validation-output/corpus.json
```

更新后的解析策略不会自动重建旧索引。仅在实际决定重新入库时，v5 才使旧切分结果失效并重新生成；见 [切分说明](DOCUMENT_CHUNKING.md)。

## 本轮验证记录

报告见 [2026-10-28 验收记录](../evals/manufacturing_lite/reports/20261028/README.md)。语料验证中 56 个已标注检查通过；扫描件原始 OCR 为 3/6 个关键字段匹配，可信定点校对后为 6/6。整页仍待复核，不能据此声称 OCR 全文准确率为 100%。NIST 长手册仍存在个别公式阅读顺序问题。

4 份原生 DOCX 使用本机 Word 导出为 PDF，与 4 份合成 PDF 的全部 10 页一起渲染检查，修复了模板附带的装饰边框，确认表格与正文未截断。真实模型评测尚未运行；不报告制造业问答准确率、响应时延或业务收益。

原分支合并还发现会话摘要实验的真实验收未通过：以默认关闭的实验代码合入，文档保留失败记录。本机 `.env` 的 `CONVERSATION_MEMORY_ENABLED` 也设为 false；未重启已有进程，需应用重启后才影响运行中的服务。
