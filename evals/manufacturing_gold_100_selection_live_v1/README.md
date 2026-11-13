# 制造业百题：引用校验与来源选择修复版验证

使用不变的 `manufacturing_gold_100_v4` 题集、来源和复核口径，针对2026-11-13引用编号校验修复及制造来源选择契约进行新的单次完整100题验证。

本批次单独授权与冻结：最多800次物理模型调用、500万预算Token、9000秒；未知用量每次预留32000。复用既有885块向量，不索引，不重试、补跑或拼接成绩。只发送既有中文公开/合成资料、问题和ERPNext/OpenMES演示白名单字段至既有百炼端点，不发送凭据、金标或旧答案。

入口为 `scripts/manufacturing_gold_v4_selection_live.py` 与 `scripts/run_manufacturing_gold100_v4_selection_isolated.py`。新平台库、专用Redis和MES故障副本承载写入和故障测试；原件和ERP来源只读复用。现有业务工作台不更新。

私有准备与证据目录：`tmp/manufacturing-gold-100-v4-selection-live-20261113-r2/`。结果待本批实际执行后另行发布。旧81/100及其他历史结果保持；本轮复核仍为助手非独立，独立人工、工厂、平台批准均0。
