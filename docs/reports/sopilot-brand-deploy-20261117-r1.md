# SOPilot品牌与B款标识部署（2026-11-17）

用户批准将SOPilot品牌和选定的B款“工序双轨”标识更新到统一8000。页面标题、侧栏、登录弹窗、帮助、页脚、作业指导追问标题及助手署名统一，浏览器图标采用同一SVG图形。

源码提交`6aeda6b`。基于实际运行的`enterprise-knowledge-manufacturing:frontend-20261117-r2`离线构建新镜像`enterprise-knowledge-manufacturing:frontend-20261117-r3`，只覆盖`index.html`、`app.js`、`styles.css`，与冻结提交逐文件哈希一致。app.js仅修改助手署名字符串，样式仅涉及品牌组件，未安装新依赖或修改业务源码。

74项Node和16项前端API测试通过。部署前重新核验三API健康、无在途模型/索引/评测任务，保存配置及数据指纹。Compose解析对比确认只改变三个API镜像；仅对`knowledge erp mes`执行`up -d --no-deps --pull never`。

部署后验证：

- 三API健康；三工作区页面、资源内容与镜像清单一致，包含SOPilot和B款原始几何路径。
- 79项真实GET通过，覆盖静态资源、未认证401、文档与历史、质量接口、ERP工序卡、MES批次步骤及映射、参考角色越权404。现有账号认证令牌只保存在内存，不落盘；8005/8006仍关闭。
- 浏览器刷新真实8000，确认SOPilot标题/标识/页脚已生效，无原品牌、预览标识或横向溢出。
- 三API全部168个业务Python和其余静态文件保持原始字节；三库47170+3355+2414共52939行指纹、184+13+13共210份原件、模型账本与另外73个容器保持。
- 未发起新问答、模型调用、评测、迁移或索引；Worker和网关不重建，数据卷、历史记录、旧镜像及备份保留。原output和原型保持。

当前本机启动入口：

```powershell
docker compose -f tmp/unified-workspace-8000-20261114-r1/compose.json -f tmp/sopilot-brand-deploy-20261117-r1/compose.override.json --profile workers up -d --pull never
```

回退本次品牌变更：

```powershell
docker compose -f tmp/unified-workspace-8000-20261114-r1/compose.json -f tmp/frontend-polish-deploy-20261117-r1/compose.override.json up -d --no-deps --pull never knowledge erp mes
```

只恢复此前前端应用，不恢复数据库或开放旧端口。私有配置与前后指纹保留在`tmp/sopilot-brand-deploy-20261117-r1/`，不提交凭据。源码及记录本地提交，未推送或合并main。

[部署后页面](assets/sopilot-brand-deploy-20261117-r1/knowledge-live.jpg)、[品牌细节](assets/sopilot-brand-deploy-20261117-r1/brand-detail.jpg)。[设计与预览过程](sopilot-brand-preview-20261117-r1.md)及旧设计截图保持。
