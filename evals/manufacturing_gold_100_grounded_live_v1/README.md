# 制造百题有据解释修复版真实模型验证入口

使用不变的 manufacturing_gold_100_v4，100题单次执行；助手非独立复核，不是盲测或人工/工厂批准。

- 入口：`scripts/manufacturing_gold_v4_grounded_live.py`。
- 协调：`scripts/run_manufacturing_gold100_v4_grounded_isolated.py`。
- 发布：`scripts/report_manufacturing_gold_v4_grounded_live.py`。
- 本批上限：800物理调用、500万预算Token、9000秒；未知用量按32000预留。
- 用户已授权本轮实施期间自动使用真实模型验证；仅既有百炼端点、同范围中文公开/合成资料及演示字段。
- 新隔离数据库与临时计算容器；原件、既有索引只读复用，索引调用0。原应用不部署。
- 不发送金标或历史答案，不重试补跑，不修改旧题集/成绩，不沿用已关闭批次余额。
- 私有证据：`tmp/manufacturing-gold-100-v4-grounded-live-20261113-r4/`；运行后不得复用。

提前停止的r3：7题/25调用，契约上下文冲突，独立保存在[r3记录](runs/20261113-r3-aborted/summary.json)，不计完整成绩、不拼分。

完整r4已结束：[90/100结构、78/100助手非独立语义](runs/20261113-r4/summary.json)。244调用/1127575预算Token，授权关闭；源码cb10984，原工作台未更新。低于上一95/82，不宣称改进。
