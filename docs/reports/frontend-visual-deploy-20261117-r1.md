# 前端交互与视觉统一部署8000（2026-11-17）

用户批准将本轮问答、作业指导、文档、管理导航及状态体验优化部署8000。源码冻结为 `5d0220d`，三API镜像为 `enterprise-knowledge-manufacturing:frontend-20261117-r5`，仅覆盖 `index.html`、`app.js`、`styles.css`、`quality.js`、`verification.js` 五个前端文件。

## 已部署内容

- 文档中心：轻量统计与筛选、文件格式图标、处理/发布状态分列、手机文档卡片；有上传权限时上传区常驻显示。
- 知识问答：手机菜单与首屏优化、输入区登录入口、回答与依据分层、详情折叠、失败保留问题及真实等待时间提示。
- 作业指导：对象选择与查询主操作集中展示，指导正文、执行记录和来源侧栏沿用文档中心风格，未知字段仍保持未知。
- 管理入口归并到质量中心，保留个人反馈入口；审核摘要、已选计数及就地加载/失败提示优化。

预览过程：[交互第一轮](frontend-task-flow-20261117-r1.md)、[文档与管理交互](frontend-management-flow-20261117-r1.md)、[文档视觉](document-center-visual-preview-20261117-r1.md)、[问答与指导视觉](qa-guidance-visual-preview-20261117-r1.md)。

## 镜像与部署

实际运行的r4已有127层。首次基于r4追加前端层的候选在启动检查时出现Docker `max depth exceeded`，当时未切换8000。

随后从r4创建未启动的临时容器并导出文件系统，以离线方式构建 `enterprise-knowledge-manufacturing:frontend-20261117-r4-flat`。对比导出前后26,372个文件系统条目：内容、类型、权限、UID/GID和链接一致；排除Docker注入的 `/etc/hosts`、`/etc/hostname`、`/etc/resolv.conf`，不比较mtime。Env、Cmd、Entrypoint、User、WorkingDir、Healthcheck、ExposedPorts、Volumes、OnBuild及Labels逐项一致。原r4镜像保留，两个检查用临时容器已移除。

在等价底包上只覆盖五个冻结前端文件，按Git提交字节核对哈希。解析后的Compose仅三个API的image变化。切换前再次核对三库业务行与账本未变，且无排队/执行中的索引或评测任务；随后只重建 `knowledge erp mes`，网关与Worker不重建。

## 验收结果

- 82项Node、17项前端API与会话包装测试通过；包装测试重复运行已有Node会话套件，不算新增独立覆盖。原159个页面ID及表单约束保留，最终176个ID无重复；三个调整JS模块的原API模板保持，auth/openmes模块不变。
- 三API健康；五个前端文件与冻结镜像、实际HTTP资源一致；每个API的168个业务Python文件及其余静态文件保持原始字节。
- 79项真实GET通过，覆盖三工作区、文档、历史、质量接口、ERP工序/作业卡、MES批次/步骤/映射，以及未认证401、越权404。8005/8006仍关闭。正常账号令牌仅在验收进程内存使用。
- 浏览器正常OIDC/PKCE登录、文档中心常驻上传区与手机布局、ERP工序及作业卡字段、已有ERP历史回答、MES批次/步骤字段复核通过；未点击查询作业要求、未发送新问题、未上传、删除、审核保存或创建评测。
- 登录页8080最初被自动审批按端口范围拦截；用户随后明确授权8080后正常复核完成。首次切到MES时会话未保持，重新正常SSO后完成全部MES检查；未改认证代码，未定位首次过渡原因，不将首次尝试称为通过。
- 浏览器日志未见警告或错误。390px文档页面无横向溢出，上传区可见。运行前、切换前、HTTP后及最终浏览器复核后的业务指纹核对通过。

| 工作区 | 保持的业务行指纹数 | 原件 | 保持的业务Python |
| --- | ---: | ---: | ---: |
| 通用 | 47,170 | 184 | 168 |
| ERP | 3,355 | 13 | 168 |
| MES | 2,414 | 13 | 168 |

共52,939行业务指纹（排除动态Worker心跳表）、210份原件保持；三库模型账本不变、无待结算新增；另外73个既有容器的身份、运行状态、重启数及配置保持。原镜像、三库备份、资料卷、ERP/MES及全部历史保留，无迁移、索引、模型调用或新质量评测。

## 当前本机启动与回退

```powershell
docker compose -f tmp/unified-workspace-8000-20261114-r1/compose.json -f tmp/frontend-visual-deploy-20261117-r1/compose.override.json --profile workers up -d --pull never
```

回退本次前端，恢复上一版r4应用：

```powershell
docker compose -f tmp/unified-workspace-8000-20261114-r1/compose.json -f tmp/sopilot-welcome-deploy-20261117-r1/compose.override.json up -d --no-deps --pull never knowledge erp mes
```

私有回退脚本为 `tmp/frontend-visual-deploy-20261117-r1/rollback.ps1`。回退不恢复SQL、不降级数据库、不开放旧端口。完整备份、指纹、构建、部署及验收证据在同一私有目录，凭据不输出或提交。

[验收摘要JSON](frontend-visual-deploy-20261117-r1.json) · [文档中心](assets/frontend-visual-deploy-20261117-r1/documents-live.jpg) · [手机文档](assets/frontend-visual-deploy-20261117-r1/documents-mobile-live.jpg) · [ERP执行字段](assets/frontend-visual-deploy-20261117-r1/erp-execution-live.jpg) · [ERP历史回答](assets/frontend-visual-deploy-20261117-r1/erp-history-live.jpg) · [MES步骤](assets/frontend-visual-deploy-20261117-r1/mes-selection-live.jpg)。

源码与部署记录在本地 `codex/frontend-workspace`，未推送或合并main。原output、prototypes及附件保持。
