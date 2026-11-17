# 知识问答与作业指导视觉预览（2026-11-17）

按用户要求，沿用[文档中心视觉候选](document-center-visual-preview-20261117-r1.md)，继续优化知识问答和作业指导，先做独立预览。本轮在已有未部署候选上仅调整 `index.html` 的作业对象分组标题及 `styles.css` 页面样式，未改业务请求或数据处理。8000仍为原部署，文档中心常驻上传区保持。

## 视觉调整

- 知识问答：检索范围改为轻量信息行，欢迎图标使用淡紫底；历史控件、用户消息、回答卡片、引用依据和辅助操作统一边框、字体与间距。输入区强调焦点和发送操作，作业条件及回答提示保留。
- 作业指导：选择区增加“当前作业对象”标题，工单、执行层级和查询按钮保持原顺序。页签使用短靛紫色选中线，指导标题使用文档图形，正文与引用保持原文本。
- 来源侧栏：字段标签与值上下排列，型号、工序与实际开工分层呈现；“未登记”保持独立提示，完整来源仍可展开。适用文件使用淡紫色卡片，未新增或推断适用性。
- 手机保留44px主要操作触控高度，不增加示例问题卡片；来源字段以两列排列并保留长值换行。
- 新样式限定在 `#qa-panel`，不扩展到文档、质量或评测页面。不格式化日期，不改原业务字段，不将生成正文转换为HTML。

## 验证

- 82项Node回归通过；前端API与会话Python包装检查17项通过（会话包装重复运行已有Node会话套件，不算额外独立覆盖）。存在既有Starlette/anyio弃用警告。
- 原159个页面ID及原表单约束保留；最终176个ID无重复，`git diff --check`通过。
- 浏览器演示覆盖登录前空态、登录后问答、回答详情、引用原文、工单与工序选择、显式查询指导、指导与执行记录切换。
- 1440×1000桌面及390×844手机视觉复核；320×740、1024×768、1265×713、2552×1308指导页面宽度检查未见页面横向溢出。浏览器预览日志未见警告或错误。
- 390×844未登录问答预览包含演示提示条，发送按钮底部约815px，位于首屏。真实手机软键盘、设备读屏器和真实业务系统未覆盖。
- 问答与指导均为现有预览脚本的固定演示回答，未调用模型；未上传、创建评测、操作Docker或运行数据库。

## 预览与截图

预览：<http://127.0.0.1:8788/>。使用本地演示登录查看两页，选择工单及工序后点击“查询作业要求”。

- [问答首页桌面](assets/qa-guidance-visual-preview-20261117-r1/knowledge-welcome-desktop.jpg) · [问答首页手机](assets/qa-guidance-visual-preview-20261117-r1/knowledge-welcome-mobile.jpg)
- [问答回答桌面](assets/qa-guidance-visual-preview-20261117-r1/knowledge-answer-desktop.jpg) · [问答回答手机](assets/qa-guidance-visual-preview-20261117-r1/knowledge-answer-mobile.jpg)
- [作业指导桌面](assets/qa-guidance-visual-preview-20261117-r1/guidance-desktop.jpg) · [作业指导手机](assets/qa-guidance-visual-preview-20261117-r1/guidance-mobile.jpg) · [执行记录手机](assets/qa-guidance-visual-preview-20261117-r1/execution-mobile.jpg)

原output、prototypes及既有未跟踪文件保留；本轮候选尚未提交、推送或部署。
