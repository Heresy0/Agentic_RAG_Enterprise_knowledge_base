# 简洁版前端部署8000（2026-11-18）

用户批准将几轮简洁版前端部署8000并推送GitHub。源码冻结为`406b993`，三API镜像`enterprise-knowledge-manufacturing:frontend-20261118-r1`；基于实际运行r6，只覆盖index.html、styles.css两个文件，全部业务JS、Python与后端行为保持。

## 本次发布

- 知识问答：浅紫色“Ask SOPilot”，移除正文重复Logo、中文副标题及输入框外的AI参考提示；保留输入框内引用说明、原问题占位文案、条件、历史与引用功能。回答采用轻量阅读层次。
- 文档中心：紧凑知识库标题、轻量统计与筛选，目录成为主内容；用户指定的上传区域保持原HTML和上传相关CSS、按原权限展开。
- 作业指导：紧凑型号与执行层级、白底选择区、指导正文及来源卡片，保留历史快照、工序/卡/批次/步骤和未知字段说明。
- 质量中心、评测与改进、我的反馈：移除深色横幅与英文装饰；紧凑标题、轻描边列表和响应式表单。所有页面用侧栏同款页标；原h1只视觉隐藏。

[问答过程](qa-quiet-preview-20261117-r1.md) · [文档过程](documents-quiet-preview-20261117-r1.md) · [指导过程](guidance-quiet-preview-20261117-r1.md) · [管理页面过程](management-quiet-preview-20261118-r1.md)。预览记录中的“未部署”描述对应当时阶段，本次发布状态以本文为准。

## 构建与验收

- 84项Node前端、17项前端API/会话包装测试通过；包装检查包含已有Node会话套件，不累加为独立覆盖。原180个唯一ID、68个控件属性集合及API入口保留。
- 构建无网络拉取，从Git冻结字节构建两个文件并核对哈希。Compose解析比较仅三个API镜像变化；只重建knowledge/erp/mes，网关与Worker不重建。
- 切换前确认三API健康、模型账本无待结算且无排队/运行的索引与评测任务；保留三库备份和旧镜像。
- 三API健康；每个API的168个业务Python及其余静态文件保持字节一致，两个替换文件与镜像和8000实际HTTP资源一致。
- 79项真实GET一次完整通过：覆盖三工作区、文档、历史、质量接口、ERP工序/卡、MES批次/步骤/映射，未认证401、越权404和旧8005/8006端口关闭。正常令牌仅验收进程内存使用。
- 浏览器正常8080 OIDC/PKCE登录，复核正式Ask SOPilot、文档上传区和390px布局、质量/评测/反馈。ERP已有历史恢复显示“历史快照 · 作业卡级”；MES切换后正常SSO登录，批次/步骤选择及步骤级标题正确。warn/error为空。
- 浏览器未发送问题、点击查询作业要求、上传、删除、保存审核、创建评测或导出版本；所有已展示回答与任务均为原历史。

| 工作区 | 本轮保持的业务行指纹 | 原件 | 业务Python |
| --- | ---: | ---: | ---: |
| 通用 | 47,182 | 184 | 168 |
| ERP | 3,355 | 13 | 168 |
| MES | 2,414 | 13 | 168 |

本轮基线共52,951行业务指纹（排除动态Worker心跳），210份原件、模型账本及另外73个容器保持。通用库本轮部署前已比上次记录多12行，不把跨轮差异归因于本次部署。切换前、HTTP与最终浏览器复核后核对一致；没有新模型、索引、评测或迁移。ERP/MES、历史、卷、备份和旧镜像保留。

## 本机启动和回退

当前启动必须使用新覆盖文件：

```powershell
docker compose -f tmp/unified-workspace-8000-20261114-r1/compose.json -f tmp/frontend-quiet-deploy-20261118-r1/compose.override.json --profile workers up -d --pull never
```

回退到上一版r6应用：

```powershell
docker compose -f tmp/unified-workspace-8000-20261114-r1/compose.json -f tmp/frontend-enhanced-deploy-20261117-r1/compose.override.json up -d --no-deps --pull never knowledge erp mes
```

回退不恢复SQL、不降级数据库、不开放旧端口。私有备份/指纹/构建/回退及验收证据在`tmp/frontend-quiet-deploy-20261118-r1/`，凭据和令牌不输出提交；原output、原型与用户附件保持。

[验收摘要](frontend-quiet-deploy-20261118-r1.json) · [问答](assets/frontend-quiet-deploy-20261118-r1/qa-live.jpg) · [文档](assets/frontend-quiet-deploy-20261118-r1/documents-live.jpg) · [手机上传区](assets/frontend-quiet-deploy-20261118-r1/documents-mobile-live.jpg) · [ERP历史](assets/frontend-quiet-deploy-20261118-r1/erp-history-live.jpg) · [MES步骤](assets/frontend-quiet-deploy-20261118-r1/mes-step-live.jpg) · [质量](assets/frontend-quiet-deploy-20261118-r1/quality-live.jpg) · [评测](assets/frontend-quiet-deploy-20261118-r1/verification-live.jpg) · [反馈](assets/frontend-quiet-deploy-20261118-r1/feedback-live.jpg)。

源码与记录在`codex/frontend-workspace`；本次按用户要求推送，远端状态以最终Git核验为准，不合并main。
