# SOPilot欢迎区域与页脚部署（2026-11-17）

用户批准将已确认的欢迎区域和页脚调整更新到统一8000。欢迎空态采用B款图形、标题“向 SOPilot 提问”和简短引用说明；图形36px、桌面标题22px，220px空态居中。型号/工序/日期说明保留在条件展开内容和输入框示例中。移除主内容底部重复品牌页脚，回答下方原提示保持。

源码提交`c4040d2`。基于实际运行的`enterprise-knowledge-manufacturing:frontend-20261117-r3`离线构建镜像`enterprise-knowledge-manufacturing:frontend-20261117-r4`，只覆盖`index.html`及`styles.css`，文件哈希与冻结提交一致；JS、业务Python、依赖和模型配置不变。

74项Node、16项前端API测试通过，159个原ID和表单约束保持。部署前重新确认三API健康且无在途模型/索引/评测任务，保存配置、三库及原件指纹。解析后Compose只有三个API镜像变化；仅重建`knowledge erp mes`，不重建网关和Worker。

部署验收：

- 三API健康，三工作区页面和资源与冻结镜像一致。
- 79项真实GET通过，覆盖文档、历史、质量接口、ERP工序卡、MES批次步骤与映射、未认证401及越权404；既有账号令牌仅内存使用，8005/8006仍关闭。
- 浏览器刷新真实8000，确认欢迎区标题和品牌图形、空态高度220px、品牌页脚移除及无横向溢出。已有回答和作业指导状态的演示切换已在[部署前预览](sopilot-welcome-preview-20261117-r1.md)核对。
- 三API全部168个业务Python与所有未改静态文件保持原始字节；三库47170+3355+2414共52939行、184+13+13共210份原件、模型账本及另外73个容器保持。
- 无新问答、模型调用、评测、迁移或索引；现有业务功能、数据卷、历史记录、旧镜像与备份保持。原output和原型未动。

当前本机启动：

```powershell
docker compose -f tmp/unified-workspace-8000-20261114-r1/compose.json -f tmp/sopilot-welcome-deploy-20261117-r1/compose.override.json --profile workers up -d --pull never
```

回退本次界面调整：

```powershell
docker compose -f tmp/unified-workspace-8000-20261114-r1/compose.json -f tmp/sopilot-brand-deploy-20261117-r1/compose.override.json up -d --no-deps --pull never knowledge erp mes
```

回退仅恢复上一版应用，不恢复数据库或开放旧端口。私有配置和前后证据位于`tmp/sopilot-welcome-deploy-20261117-r1/`，凭据不输出或提交。源码与记录本地提交，未推送/合并main。

[部署后的页面](assets/sopilot-welcome-deploy-20261117-r1/knowledge-live.jpg)。
