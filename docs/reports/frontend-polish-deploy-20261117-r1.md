# 前端精修部署（2026-11-17）

用户批准将已复核的字体、配色、控件、欢迎空白和重复提示精修版部署到统一8000，保留业务功能、数据与历史。

源码提交`fe65392`。以实际运行的`enterprise-knowledge-manufacturing:frontend-20261117-r1`为底包离线构建新镜像`enterprise-knowledge-manufacturing:frontend-20261117-r2`，仅覆盖`index.html`、`app.js`、`styles.css`，与冻结提交逐文件哈希一致；不安装或更新依赖。

部署前保存三API配置、原行/原件/代码/账本指纹和其他容器状态，确认健康且无在途模型、索引或评测任务。解析后的Compose仅改变三个API的镜像。更新仅对`knowledge erp mes`执行`up -d --no-deps --pull never`，网关与Worker不重建。

验收结果：

- 三API健康，实际8000页面与资源文件一致；浏览器确认本地字体加载、重复提示移除，无横向溢出，页面不含预览数据标识。
- 79项真实GET检查通过，覆盖三个前缀的页面/资源、正常认证后的知识库/文档/历史/质量接口、ERP工序及作业卡、MES批次步骤与映射、未认证401及参考资料用户越权404；8005/8006仍关闭。认证令牌仅在内存中使用。
- 全部168个业务Python与未修改的前端模块原始字节保持。三库47170+3355+2414共52939行指纹完全保持，184+13+13共210份原件保持，模型账本无新增或待结算变化，另外73个容器保持。
- 复用发布前74项Node测试与四种尺寸的预览复核结果；未新发问、调用模型、启动评测、迁移或重建索引。

本机当前启动入口：

```powershell
docker compose -f tmp/unified-workspace-8000-20261114-r1/compose.json -f tmp/frontend-polish-deploy-20261117-r1/compose.override.json --profile workers up -d --pull never
```

回退本次精修：

```powershell
docker compose -f tmp/unified-workspace-8000-20261114-r1/compose.json -f tmp/frontend-welcome-cleanup-20261117-r1/compose.override.json up -d --no-deps --pull never knowledge erp mes
```

回退只恢复此前前端应用镜像，不恢复数据库、不开放旧端口。原镜像、备份、卷和历史证据保留，不能单用最早的Compose启动而丢失新版前端。原output、原型和历史评测保持。

[部署后实际截图](assets/frontend-polish-deploy-20261117-r1/knowledge-live.jpg)。镜像清单、前后指纹、构建及HTTP记录保留于忽略目录`tmp/frontend-polish-deploy-20261117-r1/`，不提交私有配置与凭据。源码和记录仅本地提交，未推送或合并main。
