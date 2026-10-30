# 企业知识库分层评估报告

运行模式：offline

结论：passed_automated_checks_not_production_certification

passed仅表示自动检查通过，不代表语义评审通过或生产安全认证。

| 层 | 执行状态 | 通过 | 失败 | 错误 | 未测试/部分 | 不适用 |
|---|---|---:|---:|---:|---:|---:|
| parsing | completed | 34 | 0 | 0 | 0 | 0 |

## parsing

evaluation_type：offline_actual_project_splitters


## 模型 Token 与成本审计

```json
{
  "observed": false,
  "calls": null,
  "reported_total_tokens": null,
  "unknown_usage_calls": null,
  "unknown_usage_rate": null,
  "unpriced_calls": null,
  "unpersisted_calls": null,
  "estimated_cost_by_currency": {},
  "errors": null,
  "observed_requests": 0,
  "unobserved_requests": 0,
  "limitation": "Known usage subtotal only; unknown usage is not zero. Historical replay is not new spend."
}
```

reported_total_tokens 是已知用量的小计。未知不等于零；无定价时费用为空；不同币种分别统计。

成本按运行时价格快照估算，不等于供应商账单；重放仅展示历史用量，不产生新费用。

## 解读限制

- 未测试不能算通过；不同范围、身份、语料版本的分数不可直接比较。
- 关键词匹配不能识别所有否定、错误归因或同义表达，需人工抽检。
- 端到端事实正确性、忠实上下文、引用支持是不同指标。
- 报告可能含业务答案；原始trace包含授权文档片段，请按内部敏感资料保管，不上传知识库。
