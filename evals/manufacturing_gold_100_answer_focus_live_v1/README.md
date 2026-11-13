# 生成收敛版完整百题真实验证

2026-11-13 r8：冻结入口 `ea00ed2`，业务 `e755bcb`，使用不变v4题集单次完整100题；检索和校验未改。结构95/100、助手非独立语义81/100，79模型参与题60通过、21零模型题全部通过；不是独立盲测或工厂验收。

与旧完整r5两边均交付回答的71题相比，答全必答点62→60题、未满足必答点11→14个，无充分依据补充5→5题，**未证明两个生成目标改善**。旧r5早于先前检索回退及契约修正，总分80→81不能全部归因本次提示改动。

- [完整报告](../../docs/reports/manufacturing-gold100-answer-focus-live-20261113-r8.md)
- [结果摘要](runs/20261113-r8/summary.json)
- [100题原始回答投影与判定](runs/20261113-r8/results.jsonl)
- [100题助手非独立复核](runs/20261113-r8/reviews.json)
- [生成目标比较](runs/20261113-r8/focus-comparison.json)
- [检索/校验代码保持证据](runs/20261113-r8/fixed-retrieval-validation-proof.json)
- [供应商/规划错误记录](runs/20261113-r8/model-error-proof.json)
- [来源、账本及收尾审计](runs/20261113-r8/audits.json)
- [哈希清单](runs/20261113-r8/manifest.json)

242物理调用，866512已知Token、4未知预留后994512预算Token，1178.351秒。无重试、补跑、新索引或历史成绩拼接。新6个临时计算容器与3个临时恢复的向量依赖已停止，数据保留；原工作台未更新。授权已关闭，私有原始Trace目录 `tmp/manufacturing-gold-100-v4-answer-focus-live-20261113-r8/` 已执行不得复用，不提交私有配置或凭据。
