# 黄金候选v3七题单独验证

本目录只记录006、007、011、019、064、072、090的新单次运行，使用[百题候选v3](../manufacturing_gold_100_v3/README.md)与[冻结复核口径](../manufacturing_gold_100_v3/review_policy.json)。独立批次不代表独立盲测或独立人工评分；助手非独立复核，人工批准0。

2026-11-12 r1：结构7/7，助手非独立语义6/7，006未全过。七题均实际调用模型，20次物理调用、75,151已知及预算Token、未知用量0，无重试、补跑或索引调用。业务仍为b741bd2，未修复084或其他后端问题。

旧百题v2的82/100及全部历史结果保留，不把本轮定向成绩拼入旧分，不宣称v3百题全量成绩。

- [本轮汇总](runs/20261112-r1/summary.json)
- [逐题回答与复核](runs/20261112-r1/results.jsonl)
- [审核表](runs/20261112-r1/reviews.json)
- [来源、快照与环境核验](runs/20261112-r1/audits.json)
- [冻结输入及输出指纹](runs/20261112-r1/manifest.json)
- [完整记录](../../docs/reports/manufacturing-gold-v3-seven-20261112-r1.md)
