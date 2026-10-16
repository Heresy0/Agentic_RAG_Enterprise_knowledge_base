# 冻结题集归档

旧题集已退出默认日常评估，统一使用[当前套件](../current/README.md)及`python scripts/evaluate.py`。

| 归档 | 内容 | 当前使用方式 |
|---|---|---|
| `xinghai_v4` | 原100题、r9规则、Ragas参考、多轮及权限 | 当前main组来源，历史100题复现 |
| `xinghai_v3` | 原80题 | 100题历史构建来源，不再重复跑总体成绩 |
| `xinghai_nanqi` | 南栖20题 | 已包含在main组，可按NQ题号筛选 |
| `xinghai_multihop` | 原12题及证据链说明 | 当前multihop组来源 |
| `first_pass_generalization` | 原8题 | 当前completeness组来源 |
| `datasets` | 早期检索与答案开发/测试集 | 历史复现与兼容性回归 |
| `load` | 原8题压测负载 | 生成当前负载，不增加独立题数 |

`manifest.json`记录每个源文件的原路径、归档路径和SHA-256；所有题目、参考、事实、来源、权限及原说明原样保存。归档内README和题册保留当时表述和旧路径，不作为当前运行指南。历史报告与验收文件仍留在`evals/reports/`、`evals/acceptance/`，不移动、不改分。

原构建器、历史兼容测试的路径已迁到本目录。执行`python scripts/evaluate.py check`可以核验字节完整性。请勿手工编辑归档或向知识库上传题目、答案及标注。
