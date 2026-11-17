# 强化视觉统一部署8000（2026-11-17）

用户批准将作业指导强化视觉及其他页面延伸版部署8000后推送GitHub。源码冻结`d5268c4`，三API镜像`enterprise-knowledge-manufacturing:frontend-20261117-r6`，只覆盖index.html、app.js、styles.css三个前端文件。

## 已部署内容

作业指导采用深靛色型号展示区、工序双轨图形、悬浮选择区、文档式正文及来源卡片。新增身份区域只展示来源字段与当前执行层级，历史恢复带历史快照标识；缺失工序保持未知，字段以textContent渲染。

知识问答、文档中心、质量中心、评测与改进、我的反馈延续深色页头与淡紫卡片。文档上传区按原编辑权限常驻展开，手机统计两列。保留全部原API入口、认证、权限、预算、同意及业务行为。

[指导设计预览](guidance-enhanced-preview-20261117-r1.md) · [其他页面预览](workspace-visual-extension-20261117-r1.md)。

## 部署与验收

- 84项Node、17项前端API/会话包装测试通过；包装检查与Node有重叠，不累加为独立覆盖。原176个页面ID、表单约束及API模板保留，最终180个唯一ID。
- 从实际运行r5镜像构建，三个前端文件按Git冻结提交字节核对。Compose解析对比仅三个API镜像变化，只重建knowledge/erp/mes，网关与Worker保持。
- 三API最终健康，168个业务Python文件及其他静态文件逐字节保持；三个替换文件与镜像及8000实际HTTP资源一致。
- 79项真实GET通过，含三工作区、文档、历史、质量接口、ERP工序/作业卡、MES批次/步骤/映射，以及未认证401和越权404；8005/8006保持关闭。正常账号令牌只在验收内存使用。
- 真实浏览器正常OIDC/PKCE登录后复核ERP历史快照身份、文档上传展开和390px手机布局、问答、质量及评测页面；MES通过正常SSO重新登录，批次/步骤选择和步骤级身份正确。浏览器warn/error日志为空。
- 未发送问答、查询作业要求、上传/删除文件、保存审核或创建评测。部署前、切换前、HTTP和浏览器复核后数据指纹保持。

| 工作区 | 保持的业务行 | 原件 | 业务Python |
| --- | ---: | ---: | ---: |
| 通用 | 47,170 | 184 | 168 |
| ERP | 3,355 | 13 | 168 |
| MES | 2,414 | 13 | 168 |

共52,939行业务指纹（排除动态Worker心跳）、210份原件、模型账本及另外73个容器保持，无新模型调用、索引、评测或迁移。三库备份、原镜像及全部数据/历史保留。

## 执行中情况

首次源码提交因沙箱Git锁文件权限未成功，准备进程被中止，8000尚未切换；正常权限提交后，从d5268c4重新构建并核对冻结字节，使用已保存基线，未以旧提交镜像部署。首次部署后健康检查发生在容器启动完成前，30秒左右三API均健康后继续验收。

首次HTTP检查ERP评测列表GET返回401；新正常登录令牌后完整79项GET通过。未修改认证代码，未确定首次401原因，不将首次尝试记为通过。浏览器切换MES后按独立会话范围正常SSO登录。

## 当前启动与回退

```powershell
docker compose -f tmp/unified-workspace-8000-20261114-r1/compose.json -f tmp/frontend-enhanced-deploy-20261117-r1/compose.override.json --profile workers up -d --pull never
```

回退到上一版r5应用：

```powershell
docker compose -f tmp/unified-workspace-8000-20261114-r1/compose.json -f tmp/frontend-visual-deploy-20261117-r1/compose.override.json up -d --no-deps --pull never knowledge erp mes
```

私有证据、三库备份及rollback.ps1在`tmp/frontend-enhanced-deploy-20261117-r1/`。回退仅恢复应用，不恢复SQL、不开放旧端口；私有凭据及配置不提交。原output、prototypes和其他用户文件保持。

[验收摘要](frontend-enhanced-deploy-20261117-r1.json) · [正式文档中心](assets/frontend-enhanced-deploy-20261117-r1/documents-live.jpg) · [手机文档](assets/frontend-enhanced-deploy-20261117-r1/documents-mobile-live.jpg) · [ERP历史](assets/frontend-enhanced-deploy-20261117-r1/erp-history-live.jpg) · [MES步骤](assets/frontend-enhanced-deploy-20261117-r1/mes-step-live.jpg)。

源码与记录推送至`codex/frontend-workspace`，不合并main；推送结果以远端同名分支核验为准。
