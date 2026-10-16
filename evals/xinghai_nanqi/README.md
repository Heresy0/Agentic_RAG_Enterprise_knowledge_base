# 南栖20题增量

16道可回答、4道资料不足；配套sample_docs/xinghai_nanqi的12份合成文档。
本目录只保存新增题，完整100题在evals/xinghai_v4。问题和答案不入库，单轮题互不保留上下文。
覆盖历史/现行标准、完整日与半日、累计量、订阅对象绑定、累积重试时钟、跨库证据链、计划/实际及拒答边界。

```powershell
python scripts/evaluate_system.py --mode live --config evals/system_config.local.json --dataset-dir evals/xinghai_nanqi --corpus output/knowledge-corpus-100 --layers retrieval,rerank,context,answer --allow-model-calls --save-traces
```

只在已授权的环境执行真实调用。没有多轮/权限状态用例，不使用默认all。语义检查由人工执行，自动代理不代表独立语义准确率。
