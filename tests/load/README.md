# 多租户 QA 性能压测

Locust 场景直接调用受 OIDC、知识库权限、租户限流和并发限制保护的
`POST /api/qa`，不会绕过正式业务链路。访问令牌只从环境变量读取，原始 CSV
写入已被 Git 忽略的 `data/load-tests`。

## 当前父子块与 Agent 版本的有界压测

新入口为 `scripts/run_load_test.py`，保留下方旧命令的兼容性，不修改 API、RAG 算法、权限或监控组件。
2026-10-14 的实测、工具误判与验证边界见 [新版压测验收记录](../../docs/LOAD_TEST_ACCEPTANCE_20261014.md)。原始门禁结果未改写，尚未重跑修正后工具的完整付费场景。
使用 [固定 8 题负载](current_workload.jsonl)：3 道简单业务问题、3 道比较/多跳问题、2 道拒答问题，逐字复用现有 80/12 题的原问题及可回答性标注。分组表示负载类型，不保证实际选择某条 RAG 路由；运行时路由使用现有指标观察。

| profile | 负载 | 建议用户数 | 请求上限 |
| --- | --- | ---: | ---: |
| simple | 3 道简单业务问题 | 2 | 20 |
| complex | 3 道比较/多跳问题 | 2 | 12 |
| mixed | 全部 8 题，循环均衡发出 | 2 | 24 |
| governance | 简单题，用突发并发验证 429 | 6 | 12 |

新入口默认查询全部当前授权知识库；默认负载需要 Alice 的四库读取权限。自定义单库负载可传 `--knowledge-base-id` 和匹配的 `--workload`，不能让单库账号回答跨库标注问题。
令牌读取自被忽略的根目录 `.env.evaluation`：`ENTERPRISE_KB_ACCESS_TOKEN=...`，不出现在命令参数、CSV 或报告中。真实调用须显式 `--allow-model-calls`。

先验证配置，不读令牌、不联网、不调用模型：

```powershell
python scripts/run_load_test.py --profile mixed --max-requests 24 --users 2 --preview --output-dir data/load-tests/check-current --dry-run
```

准备有效令牌后先运行治理场景，再运行混合场景，使用不同输出目录保存每次原始记录：

```powershell
python scripts/run_load_test.py --profile governance --users 6 --max-requests 12 --run-seconds 30 --output-dir data/load-tests/current-governance --allow-model-calls
python scripts/run_load_test.py --profile mixed --users 2 --max-requests 24 --run-seconds 120 --preview --output-dir data/load-tests/current-mixed --allow-model-calls
```

配置上限为 1000 次 QA、16 用户，单进程运行；不支持 Locust master/worker。请求发出前预占额度，达到上限后等待已发出的请求/预览结束；运行期限先到则停止派发并允许在途请求收尾。未发完计划请求、缺失记录或预览未结束不能记为通过。请求上限限制的是 QA HTTP 请求数，不是模型调用数；RAG 内部仍使用已有预算。
每个实际可回答 case 最多读取一次引用预览，因此 mixed 最多增加 6 次 GET；治理场景不混入预览。

启动前检查令牌剩余有效期至少覆盖运行期限、请求收尾时间和 30 秒余量，再通过受保护的知识库列表验证认证与授权。只解码 JWT 的 exp 不等于验签，实际签名及其他声明由 API 验证。令牌不足时在发出 QA 前停止，不自动获取账号密码或绕过认证。

产物包括 `report.json`、`observations.json`、`final_stats.csv`、`final_failures.csv`、Locust 周期 CSV/HTML 和日志，全部保存在忽略目录。最终 CSV 由 Locust 公共导出接口在停止时生成，避免周期采样遗漏最后请求。逐请求记录仅保留题号、分组、状态、可回答性、引用数量、阶段耗时和请求 ID，不保存问题、答案、引用正文或 JWT。

报告分别统计：

- QA HTTP 成功率、吞吐量、P50/P95/P99；预览耗时单独统计，不混入 QA 分位数。
- 按负载分组的可回答/拒答数量及标注匹配情况。合法 200 拒答可以是 HTTP 成功，但错误拒答会使可回答性检查失败，不宣称语义正确。
- 当前响应契约：有答案须有有效引用身份，拒答须有原因；阶段耗时为有限非负值。启用父块检查时，有答案须带父块引用。
- 规划、Agent、生成、反馈、再生成、修复及最终校验的阶段分布；未出现的阶段标为无样本，不能记为 0 毫秒。
- 已有 Prometheus 路由、Agent 动作、修复、反馈、预算、终态和模型用量计数的前后差值。不新增监控组件；计数是整个 API 进程的窗口数据，包含同时发生的其他流量，不能当作逐请求路由标签或精确账单。进程重启/计数下降使窗口不可比较。

门禁保留成功 QA 的 P95 ≤ 15000ms，并要求计划请求数/负载覆盖完整、零 HTTP 失败及 5xx、CSV 与记录数量一致、可回答性匹配、预览契约有效、指标窗口可比较。治理模式允许已分类的 429，但要求至少一次有效 200 和一次治理拒绝；429 的低延迟不能稀释成功 QA 的 P95。缺失 CSV 分位数记为未知，不当作零。

默认父块检查不修改服务器配置。做关闭父块的对照时必须先显式配置并重启服务，再传 `--child-only`；该参数只关闭压测中的父块 ID 要求。小规模结果不能证明企业级并发容量；包含初始请求、没有独立预热或多轮重复，也不与旧版本不同负载的 P95 作严格因果比较。

手动重新汇总完整产物：

```powershell
python scripts/summarize_load_test.py --stats data/load-tests/current-mixed/final_stats.csv --failures data/load-tests/current-mixed/final_failures.csv --observations data/load-tests/current-mixed/observations.json --output data/load-tests/current-mixed/recomputed.json --scenario steady --min-requests 24 --min-successful-requests 24 --max-failure-rate 0 --max-p95-ms 15000 --max-server-errors 0
```

新报告继续可以作为 `scripts/evaluate_system.py --performance-report <report.json>` 的输入。导入报告不等于执行了一次新压测。

## 前置条件

1. 使用 `docker compose up -d --build` 启动完整环境。
2. 确认 `/health/ready` 返回 `status=ok`。
3. 获取 Alice 的短期 JWT，确保目标知识库已经完成文档索引。
4. 在当前 PowerShell 窗口设置配置：

```powershell
$env:ENTERPRISE_KB_ACCESS_TOKEN = $token
$env:LOAD_TEST_KNOWLEDGE_BASE_ID = `
  "8e4af664-22c1-480e-9393-36ac401da0f5"
$env:LOAD_TEST_CATEGORIES = "technical"
```

JWT 不应写入 `.env`、命令参数或报告文件。一次测试时间应短于令牌剩余有效期。

## 稳定负载基线

该模式下只有合法的 200 响应算成功，429 和其他非 200 状态都算失败：

```powershell
$env:LOAD_TEST_MODE = "steady"

python -m locust `
  -f tests/load/locustfile.py `
  --headless `
  -u 2 `
  -r 1 `
  -t 3m `
  --csv data/load-tests/qa_steady `
  --csv-full-history `
  --html data/load-tests/qa_steady.html
```

汇总并执行基线门禁：

```powershell
python scripts/summarize_load_test.py `
  --stats data/load-tests/qa_steady_stats.csv `
  --failures data/load-tests/qa_steady_failures.csv `
  --output evals/reports/load_test_steady_v1.json `
  --scenario steady `
  --min-requests 20 `
  --min-successful-requests 20 `
  --max-failure-rate 0.01 `
  --max-p95-ms 15000 `
  --max-server-errors 0
```

汇总命令在门槛未通过时返回非零退出码，因此可直接接入 CI 或发布门禁。

## 资源治理压力验证

该模式刻意制造超过租户并发上限的请求。200 和 429 都是预期业务结果，任何
5xx、非法响应结构或连接失败仍然记为失败：

Locust 会根据响应头把治理拒绝分别记录为
`/api/qa [429-concurrency]` 和 `/api/qa [429-rate]`，方便判断实际触发的是
并发槽位还是滑动窗口限流。

```powershell
$env:LOAD_TEST_MODE = "governance"
$env:LOAD_TEST_WAIT_MIN_SECONDS = "0.1"
$env:LOAD_TEST_WAIT_MAX_SECONDS = "0.2"

python -m locust `
  -f tests/load/locustfile.py `
  --headless `
  -u 8 `
  -r 8 `
  -t 45s `
  --csv data/load-tests/qa_governance `
  --html data/load-tests/qa_governance.html

python scripts/summarize_load_test.py `
  --stats data/load-tests/qa_governance_stats.csv `
  --failures data/load-tests/qa_governance_failures.csv `
  --output evals/reports/load_test_governance_v1.json `
  --scenario governance `
  --min-requests 8 `
  --min-successful-requests 1 `
  --max-failure-rate 0 `
  --max-p95-ms 15000 `
  --max-server-errors 0 `
  --min-throttled-requests 1
```

测试期间可同时查看 Grafana 的 `Enterprise Knowledge RAG` 看板，或直接查询
Prometheus 的请求耗时、QA 结果、限流和并发治理指标。测试结束后清理令牌：

```powershell
Remove-Item Env:ENTERPRISE_KB_ACCESS_TOKEN
Remove-Item Env:LOAD_TEST_KNOWLEDGE_BASE_ID
Remove-Item Env:LOAD_TEST_MODE
Remove-Item Env:LOAD_TEST_CATEGORIES
```
