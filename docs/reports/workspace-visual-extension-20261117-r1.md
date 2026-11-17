# 页面视觉语言延伸预览（2026-11-17）

用户在认可作业指导强化预览后，要求将视觉语言延伸到其他页面。本轮延续独立预览范围，未操作8000部署、Docker、数据库、模型、索引或Worker。

## 设计与范围

- 知识问答：深靛渐变欢迎区域、工序双轨图形、淡紫顶边输入卡片与回答依据卡片。保留作业条件、回答提示和原对话行为，不恢复示例问题卡片。
- 文档中心：深色知识库页头、悬浮文档统计、白色上传区域、独立筛选卡片和浅紫表头。上传区仍展开，按原有编辑权限显示；手机统计改为两列。
- 质量中心、评测与改进、我的反馈：共享深色页头与双轨图形，筛选或表单浮于页头下缘；审核和样本卡片使用淡紫顶边。权限、同意、预算及状态说明保留，无虚构成绩或进度。
- 修正320px窄屏长页名挤压顶部账号按钮的布局；评测表单宽度与页面对齐。

本轮仅新增HTML展示类/装饰SVG及CSS样式。作业指导上一轮新增的app.js身份展示与测试仍在同一未提交候选中，本轮没有继续修改业务行为。

## 验证

- `node --test tests/frontend_conversations.test.cjs tests/frontend/auth.test.mjs`：84通过。
- `python -m pytest tests/test_frontend_api.py tests/test_frontend_conversations.py -q`：17通过（包含前端包装检查，与Node数量不作独立累加）；原有anyio弃用提醒。首次沙箱TestClient未结束，中止后允许本地测试线程执行通过，未修改测试规避。
- HTML解析核对：保留HEAD全部176个ID与表单控件约束；候选180个唯一ID，额外4个来自上一轮作业指导预览。
- 浏览器演示登录后复核文档、问答、质量审核、评测表单、反馈；未创建评测任务或上传文件。桌面1440×1000、手机390×844视觉检查，320px评测及文档和1024px文档无页面横向溢出；控制台warn/error为空。
- 预览仅内存演示数据，不作为真实ERP/MES、后端权限或生产数据验收。

## 预览与截图

[独立预览](http://127.0.0.1:8792/erp/)，运行入口：`.venv/Scripts/python.exe scripts/preview_frontend.py --port 8792`。选择导航中的目标页面；管理或上传需在演示登录后选择“工艺与作业指导”知识库。

- [文档桌面](assets/workspace-visual-extension-20261117-r1/documents-desktop.jpg) · [文档手机](assets/workspace-visual-extension-20261117-r1/documents-mobile.jpg)
- [问答桌面](assets/workspace-visual-extension-20261117-r1/qa-desktop.jpg) · [问答手机](assets/workspace-visual-extension-20261117-r1/qa-mobile.jpg)
- [质量桌面](assets/workspace-visual-extension-20261117-r1/quality-desktop.jpg) · [质量手机](assets/workspace-visual-extension-20261117-r1/quality-mobile.jpg)
- [评测桌面](assets/workspace-visual-extension-20261117-r1/verification-desktop.jpg) · [评测手机](assets/workspace-visual-extension-20261117-r1/verification-mobile.jpg)
- [反馈桌面](assets/workspace-visual-extension-20261117-r1/feedback-desktop.jpg)

本轮未提交、推送或部署；8000保留既有版本。原output、原型及用户其他文件保持。
