# 知识问答首页精简（2026-11-17）

按用户要求移除欢迎区域的三张示例问题卡片，同时清理专属点击监听和响应式样式。欢迎标题、说明、提问框和作业指导的“查询作业要求”入口保留。

源码提交 `6f07319`，部署镜像 `enterprise-knowledge-manufacturing:frontend-20261117-r1`。基于当前运行的 `frontend-20261116-r2` 离线构建，仅覆盖 `index.html`、`app.js`、`styles.css`，没有重新安装依赖。

74项既有Node测试通过；镜像内三个文件与冻结提交一致，三工作区9个HTTP GET核验首页和资源一致，浏览器复核卡片已移除。三个API均健康；全部168个业务Python原始字节、52939行数据库指纹、210份原件、模型用量账本和另外73个容器保持。只重建三个API，没有迁移、索引、评测或模型调用。

本机后续启动：

```powershell
docker compose -f tmp/unified-workspace-8000-20261114-r1/compose.json -f tmp/frontend-welcome-cleanup-20261117-r1/compose.override.json --profile workers up -d --pull never
```

回退本次前端精简时，使用原Compose加 `tmp/frontend-workspace-deploy-20261116-r1/compose.override.json`，仅对 `knowledge erp mes` 执行 `up -d --no-deps --pull never`。原镜像和旧部署记录保留，不恢复数据库、不开放旧端口。

私有部署配置、前后指纹及截图保留在 `tmp/frontend-welcome-cleanup-20261117-r1/`，不提交凭据。源码与本记录仅本地提交，未推送或合并main。原output、原型和历史结果保持。
