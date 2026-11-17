# SOPilot品牌本地预览（2026-11-17）

按用户批准，将前端品牌改为SOPilot，标识和相关文案一起调整。本记录保留部署前的设计与预览过程；用户后续批准部署，当前运行状态见[品牌部署记录](sopilot-brand-deploy-20261117-r1.md)。

- 侧栏采用适配原208px宽度的SOPilot文字标识，保留原紫色和中文标语。
- 使用用户最终选定的B款“工序双轨”SVG标识，侧栏和登录弹窗复用；浏览器图标使用同一形状的内嵌SVG，不新增资源路由或外部依赖。
- 更新页面标题、描述、首页无障碍名称、页脚、登录标题和使用帮助；作业指导追问标题改为“询问 SOPilot”，助手署名改为“SOPilot”。中文说明为“工艺知识与作业指导平台”。
- app.js仅替换一处署名字符串，CSS仅调整品牌组件，现有布局、功能入口、业务请求和历史内容不变。既有前端API测试仅同步预期品牌名。

74项Node及16项前端API测试通过；原158个ID与表单约束保持，仅新增一个SVG symbol。浏览器复核桌面1440×1000及手机390×844，品牌文字未溢出，也未与工作区选择器重叠；登录弹窗、帮助标题、助手演示回答署名正确，未见控制台警告或错误。演示登录和问答均由独立预览脚本提供，不使用真实凭据、数据库或模型。

预览：<http://127.0.0.1:8788/>。复用现有内存预览进程；若已停止，可执行：

```powershell
.venv/Scripts/python.exe scripts/preview_frontend.py --port 8788
```

[桌面效果](assets/sopilot-brand-preview-20261117-r1/desktop.jpg)、[登录弹窗](assets/sopilot-brand-preview-20261117-r1/login.jpg)、[手机效果](assets/sopilot-brand-preview-20261117-r1/mobile.jpg)、[助手署名](assets/sopilot-brand-preview-20261117-r1/answer.jpg)。私有检查记录在`tmp/sopilot-brand-preview-20261117-r1/`。

8000仍为精修版`frontend-20261117-r2`及原“知序”品牌，原启动/回退入口不变。本轮未操作Docker、部署、运行数据库或模型；历史报告、原output及原型保持。当前修改未提交、推送或合并。

用户反馈首版S标识后，仅调整品牌图形与对齐：新版为导航箭头，登录图形放大至44px，手机保持原布局且未见横向溢出。[新版标识](assets/sopilot-brand-preview-20261117-r2/brand-detail.jpg)、[新版页面](assets/sopilot-brand-preview-20261117-r2/desktop.jpg)、[新版登录](assets/sopilot-brand-preview-20261117-r2/login.jpg)。先前S版截图保留作设计历史；本次未改JS、业务、模型或部署。

另按用户要求制作四个独立Logo方向（A折页P、B工序双轨、C指北针、D知识翼），对比页为<http://127.0.0.1:8790/>，源SVG和页面位于忽略目录`tmp/sopilot-logo-options-20261117-r1/`，仅本地静态设计展示。未替换8788当前导航箭头标识，也未操作8000部署。[四款对比](assets/sopilot-logo-options-20261117-r1/comparison.jpg)。

用户最终选择B款“工序双轨”，已将对比稿的原始几何路径应用到8788侧栏、登录弹窗和浏览器图标，保持紫色及组件尺寸，浏览器复核图形显示正常。仅更新index.html内SVG及图标，不改业务逻辑或部署。[B款应用](assets/sopilot-brand-preview-20261117-r3/desktop.jpg)、[标识细节](assets/sopilot-brand-preview-20261117-r3/brand-detail.jpg)、[登录弹窗](assets/sopilot-brand-preview-20261117-r3/login.jpg)。

最终SOPilot品牌及B款标识随后以`6aeda6b`冻结并部署8000，详见[部署记录](sopilot-brand-deploy-20261117-r1.md)。以上未部署说明均为对应设计阶段的状态，旧截图保留。
