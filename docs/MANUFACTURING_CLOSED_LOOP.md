# 制造业知识问答闭环

本轮在 `codex/manufacturing-closed-loop` 实施。毕设副本固定在3049685，不参与改动；现有制造业语料、冻结题集和历史报告保留。验收使用隔离环境及新增轮次。

目标流程：上传原件 → 解析与清洗 → 预览／复核 → 发布 → 会话问答／澄清 → 查看原件与证据 → 反馈审核 → 回归评测 → 验证关闭。

## 实施与验收清单

- [x] 文档质量发布：新增资料默认待发布，展示解析方式、质量标记、清洗记录和分页分块；复核绑定文档版本，发布／撤回统一控制检索与原文访问；更新成功后重新复核，失败保留旧版本。
- [x] 会话交互：回答、澄清、拒答独立表达；补充条件后续答，切换对象不沿用旧条件；明确的型号／工序／业务日期作为用户条件，不作为知识事实。
- [x] 证据追溯：引用预览显示物理页范围与质量；原文件访问重新鉴权并校验当前版本；请求阶段观察按权限与大小上限保存，不记录凭据或隐藏推理。
- [x] 解析路由：本轮按用户确认仅完成普通解析与OCR闭环；视觉解析在模型配置完成后单独接入与实测。本轮保留明确未支持的复杂图表边界，不以字数增加认定OCR成功。
- [x] 反馈验证：复用已有工作台、样本版本、预算Worker与验证关闭；使用制造业用例贯通，所有助手操作标为非独立流程验收，不把已知失败伪装成已修复。
- [x] 运行交付：正式参数化启动、预检、上传发布、审计与验收入口，明确租户、原件指纹、请求与模型调用预算；核验API和两类Worker版本；明确永久文档错误与临时失败。
- [x] 分层验收：API／数据库／前端回归；真实原生和OCR样本分别记结果，视觉标记未配置；澄清与续答、发布撤回、版本更新、权限变更分别检查；保留首轮40题，新增轮次后在配置固定时运行80题。

历史文档缺少发布元数据时维持既有可检索行为，不自动迁移或重建旧基线；新资料必须明确发布。下文随实际实施补充结果，勾选仅代表对应功能及必要验证完成。

## 已实现的边界

发布状态保存在文档元数据中，无数据库迁移。新上传资料即使索引成功，也须由已有编辑权限的用户绑定当前文档版本与复核修订发布；OCR必须明确确认识别限制。更新成功后回到待复核，更新失败保留旧版本。SQL、关键词、向量结果回填、父块和引用预览统一检查状态，撤回后新请求不再使用该资料。撤回不等于撤销用户权限，历史快照按原有权限与反馈共享规则保留。

原件通过受鉴权的接口获取，校验托管路径、当前版本、文件大小和SHA-256；PDF定位使用物理页。父块页范围由其实际子块计算，不将命中子块页码冒充整个父块页码。未托管的历史CLI原件没有任意文件下载能力。

回答协议区分`answer / clarify / refuse`。澄清须有缺失条件和直接追问，不带事实结论；模型同时提出补查时，程序取消冗余补查，不从知识库猜用户条件，也不改变模型的结论或可回答性。补充条件经会话改写后续答。型号、工序、业务日期和工单号是可选用户输入，修改条件会开启新对话。本轮没有ERP/MES连接与实时读写能力。

私有快照增加候选ID、父块ID、版本、名次、有限数值分数和正文指纹；不保存额外候选正文、凭据或隐藏推理。每阶段最多40行、3轮，总计500行和200KB，并标记截断；截断记录不能证明更低名次的证据没有被召回。最终上下文仍单独校验完整性。

文档复核与反馈样本审核是两个入口。反馈仍须明确授权共享和导出，样本绑定当次快照、来源版本与审核修订。新增预期行为可防止将普通拒答算作澄清通过。OCR关键数值损坏的真实反馈保留为`needs_evidence / draft`，不自动编造参考数值或批准导出。

## 本轮验收

详细结果见[本轮验收记录](../evals/manufacturing_closed_loop/reports/20261029-r1/README.md)。本轮没有修改冻结语料、题目或旧报告，也没有调整OCR数值来迎合题目。

| 项目 | 结果 |
| --- | --- |
| 后端回归 | 1477通过，55跳过 |
| 前端回归 | 30通过 |
| 中文原件 | 18份；17就绪并发布，1失败；1034子块和向量，538父块 |
| 文档生命周期 | 临时合成TXT的8项真实HTTP检查通过；完成后删除临时资料 |
| 开发集40题 | 40完整响应；自动代理29/40；普通20/30，行为9/10，澄清3/4 |
| 留出集80题 | 75次HTTP请求；73完整响应、2次503超时；5题来源受阻；自动代理37/80 |
| 留出分组 | 普通17/30，OCR5/30，行为15/20；OCR分母包含5来源受阻与1超时 |
| 反馈闭环 | 2样本、2固定版本、2预算评测任务、带指纹复核及1次演示关闭；另留1个OCR问题待补证据 |

自动代理评分不是语义准确率。旧开发集原r10得分30/40仍保留；按新增行为约束的同一r11评分重放为27/40，新轮29/40。不能直接把不同口径或助手非独立复核的结果相比较。留出集为首次完整运行，但来源标注此前用于解析检查，且本轮已查看结果，不称独立盲测。

普通资料仍有证据选择、关键要点遗漏和词面评分误差；N10仍未正确追问。OCR能入库不代表关键数值可靠，例如卡规检查仪原文中的温度字符仍有损坏。两次503的实际账本错误均为生成调用`APITimeoutError`，约60秒，未重试。困难扫描件MFG-ZH11在第1次解析失败后停止自动重试，保留原件供复核。

本轮仅验证普通解析与OCR闭环。视觉解析没有配置、没有调用、没有成绩。后续应先实测复杂图表和关键数值的视觉解析，再决定是否替代OCR结果。

## 运行入口

私有运行目录必须放在忽略的`tmp/`中。凭据、令牌、原始快照留在本机，不提交；每轮使用新目录，已有付费请求证据不可覆盖或自动重放。

```powershell
$runDir = 'tmp/mfg-demo-新轮次'
docker build -f Dockerfile.manufacturing -t enterprise-knowledge-manufacturing:closed-loop .
docker compose -f compose.yaml -f compose.manufacturing.yaml up -d --no-deps --no-build api worker quality-worker
.venv/Scripts/python.exe scripts/manufacturing_workflow.py before --run-dir $runDir
.venv/Scripts/python.exe scripts/manufacturing_workflow.py provision --run-dir $runDir --apply
.venv/Scripts/python.exe scripts/manufacturing_workflow.py tokens --run-dir $runDir --apply
.venv/Scripts/python.exe scripts/manufacturing_workflow.py upload --run-dir $runDir --apply --allow-model-calls
.venv/Scripts/python.exe scripts/manufacturing_workflow.py status --run-dir $runDir
# 就绪后检查原件与分块，再明确填写复核说明；OCR发布须额外确认。
.venv/Scripts/python.exe scripts/manufacturing_workflow.py publish --run-dir $runDir --apply --acknowledge-ocr --review-note '填写实际检查范围及剩余限制'
.venv/Scripts/python.exe scripts/manufacturing_workflow.py audit --run-dir $runDir
```

派生镜像复用本机已验证基底，并检查`requirements.txt`完全一致；没有该基底或依赖发生变化时，先按原`Dockerfile`构建，再用`--build-arg BASE_IMAGE=实际基底镜像`指定。核验API、索引Worker与评测Worker使用相同镜像后再验收，不重启基础数据服务。

`accept_manufacturing_lifecycle.py`运行合成资料生命周期，最多触发2次小文档索引。`accept_manufacturing_workflow.py`分`ask / samples / evaluate / verify`，先查看实际答案，再批准样本；新应用版本通过`--evidence-dir`保留独立轮次。`verify`须提供绑定`sample_id / response_binding`的明确复核JSON，比较未通过时不关闭。演示复核标明助手非独立操作，同配置重复运行不构成质量提升证明。

HTTP题集使用`run_manufacturing_http_acceptance.py`，须明确`--allow-model-calls`和`--max-questions`。测试集源资料受阻时，默认整轮拒绝启动；显式`--record-unavailable-sources`会为受阻题记错误、保留分母且不发问答请求。该批处理只有题数上限，不声称金额或整个批次Token硬上限。质量Worker的演示任务单独强制每任务2题、24次调用、400000 Token、每调用预留32000 Token、240秒；未知价格须明确确认才能验证关闭。

`report_manufacturing_acceptance.py`离线分组评分，`--collect-ledger`只读对账；`manufacturing_workflow.py after / preserve`检查原有业务表与基线文件。原始结果及超时不覆盖、不补跑后择优记分。
