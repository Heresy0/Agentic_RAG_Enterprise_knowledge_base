# SOPilot欢迎区域本地预览（2026-11-17）

本记录保留部署前设计过程。用户后续批准部署，当前状态与启动入口见[部署记录](sopilot-welcome-deploy-20261117-r1.md)；以下未部署说明为当时状态。

按用户确认的搭配替换欢迎区域：B款双轨品牌图形（32px、无底色），标题“向 SOPilot 提问”，说明“查找工艺规范与作业要求，沿引用核对原文。”。首版桌面标题24px/450字重，图形到标题20px、标题到说明10px，手机继续使用原有字号断点。

型号、工序和日期的说明移到输入框上方的“补充作业条件”入口，并在展开内容中保留普通模式边界及条件变化说明。整体布局、输入框及业务逻辑保持，仅改index.html和styles.css。

桌面及390×844手机浏览器复核通过，无页面横向溢出，条件入口保持一行，展开后四个原字段保留。原159个页面ID及表单约束保持。本次未修改JS、业务、模型、数据库或Docker，未更新8000；当前修改未提交或推送。

[欢迎区域](assets/sopilot-welcome-preview-20261117-r1/welcome-detail.jpg)、[桌面页面](assets/sopilot-welcome-preview-20261117-r1/desktop.jpg)、[手机页面](assets/sopilot-welcome-preview-20261117-r1/mobile.jpg)。独立内存预览：<http://127.0.0.1:8788/>。若已停止，可执行`.venv/Scripts/python.exe scripts/preview_frontend.py --port 8788`。

8000仍为下述已发布版本：`frontend-20261117-r3`，SOPilot品牌及B款标识，启动/回退入口保持[品牌部署记录](sopilot-brand-deploy-20261117-r1.md)。原output、原型及历史记录保持。

用户反馈不协调后，当前候选将图形调整为36px、桌面标题22px，间距收为16/8px；仅欢迎空态使用220px最小高度并垂直居中，实测图形至上方工具栏间隔72px、说明至下方条件入口间隔70px。去掉条件入口旁的重复短提示，型号/工序/日期说明仍保留在展开内容和输入框示例中。390×844手机无横向溢出；演示问答后回答区恢复block布局，作业指导空问答区高度仍为0。未改JS/业务或8000部署。[协调版区域预览](assets/sopilot-welcome-preview-20261117-r2/context-detail.jpg)、[手机](assets/sopilot-welcome-preview-20261117-r2/mobile.jpg)。

按用户对品牌页脚的反馈，本地候选已移除主内容底部重复品牌/标语及分隔线，并清理其桌面/手机专属样式；回答下方原提示保持，159个ID及业务功能不变。浏览器确认页脚不存在、页面无横向溢出。[移除页脚后的页面](assets/sopilot-welcome-preview-20261117-r3/desktop.jpg)。仍未更新8000。

最终候选以`c4040d2`冻结并部署到8000，旧版预览截图保持。
