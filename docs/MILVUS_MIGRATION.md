# Milvus 向量存储与迁移

当前向量后端为 Milvus 3.0.2 Standalone，通过 REST v2 调用，不提供 pgvector 运行时切换或回退。
PostgreSQL 16 保留租户、部门、权限、文档、正文分块、pg_search BM25、对话与审计数据。

## 本地部署

```powershell
docker compose up -d --build --wait --wait-timeout 180
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

Milvus 使用独立的 etcd、MinIO 和三个命名数据卷。一次性初始化服务设置 Milvus 卷的运行用户权限。
宿主机 REST 地址是 `http://127.0.0.1:19531`，容器内为 `http://milvus:19530`。
19531 避开本机其他项目的 19530；MinIO 和 etcd 不映射宿主机端口，Milvus 管理端口也不对外映射。
MinIO 使用 Milvus 官方维护的固定版本镜像 `milvusdb/minio:RELEASE.2024-12-18T13-15-44Z`。
本地对象存储凭据仅用于此演示网络；生产部署应另行配置访问凭据、网络隔离及 Milvus 认证。
镜像与卷初始化参考 [Milvus 官方 Compose](https://github.com/milvus-io/milvus/blob/master/deployments/docker/standalone/docker-compose.yml)。

`.env.example` 提供 `MILVUS_URI`、`MILVUS_COLLECTION`、`MILVUS_TIMEOUT_SECONDS` 和可选 `MILVUS_TOKEN`。
API 和 Worker 在启动时验证集合字段、1024 维向量、COSINE 度量和集合加载状态；集合默认使用 HNSW。

## 检索与版本发布

向量集合仅保存分块 ID、租户 ID、知识库 ID、文档 ID、索引版本、写入时间与向量，不保存正文。

1. PostgreSQL 在当前授权范围内读取 `ready` 文档的 `vector_generation`。
2. Milvus 的 ANN 查询同时过滤租户、知识库及这些已发布版本，使用 Strong 一致性。
3. SQL 回查命中分块，重新核验权限范围、文档状态与索引版本，正文和引用均取自 SQL。

空授权范围直接返回空结果。Milvus 的 COSINE 相似度转换为 `1 - similarity`，保持上层距离语义。
Milvus 故障触发问答 503；不默默返回空向量结果。引用预览继续由 SQL 分块与现有权限链处理。

文档更新先生成独立的新版本，写入 Milvus 并确认完整可见，再提交 SQL 的分块、文档版本及状态。
发布事务按租户、文档顺序加锁；短暂 Milvus 写入期间会持有该事务，外部嵌入模型调用在事务外完成。
失败时旧 SQL 版本保留。Milvus 主键由分块 ID 与随机索引版本共同计算，相同文件重试也不会覆盖当前向量。
幂等跳过同时检查 SQL 和 Milvus 的数量；远端向量缺失时可以通过重新入库重建。

替换旧版本或删除文档时，在同一 SQL 事务写入 `vector_sync_jobs`。
删除任务不依赖文档外键，文档级联删除后仍能由 Worker 重试。最多自动尝试 10 次，最终失败保留任务。
SQL 发布失败产生的未发布向量由 Worker 每小时回收，保留至少 24 小时；单次最多检查 1000 条孤儿记录。
回收查询排除所有 SQL 引用版本，避免大量有效记录阻塞孤儿扫描。

## 旧数据迁移

首次从旧版本升级前，应停止 API 和 Worker 写入并保留 PostgreSQL 完整备份。
迁移 `d0f2a4c6e809` 在一个 SQL 事务中完成以下操作：

1. 每份文档的旧向量和原分块 ID 写入持久化导入任务。
2. 回填当前索引版本。
3. 删除 `document_chunks.embedding` 与 SQL HNSW 索引。

随后 `scripts/sync_vectors.py` 幂等导入 Milvus。成功任务清空向量载荷，避免在 PostgreSQL 重复保存向量。
Compose 的 API 启动命令先执行迁移和 `sync_vectors.py --require-imports`，未完成导入时不会开放问答入口。

```powershell
python -m alembic upgrade head
python scripts/sync_vectors.py --audit
```

当前 `pg_search 0.25.11` 声明了对 `vector` 扩展的依赖，因此数据库镜像仍安装该扩展。
历史 Alembic 迁移也需要 Python `pgvector` 包。保留这些依赖不代表保留旧向量列或旧检索实现。
不要执行 `DROP EXTENSION vector CASCADE`，它会连同 pg_search 与 BM25 索引一起删除。

## 检查与故障恢复

```powershell
# 检查每个 SQL 当前版本在 Milvus 中的向量数量
python scripts/sync_vectors.py --audit

# 解决服务故障后重试已达到上限的同步任务
python scripts/sync_vectors.py --retry-failed --audit
```

脚本不会调用嵌入模型。审计检测当前版本向量缺失和数量不一致；它不替代逐向量内容校验或答案质量评估。
Milvus 数据卷丢失且成功导入载荷已经清空时，需要恢复 Milvus、etcd、MinIO 的一致性备份，或重新入库生成向量。
集合重建后重新入库会检查远端缺失，不会因文件哈希相同而跳过。

`/health/ready` 包括 Milvus 状态。Prometheus 导出 `enterprise_vector_store_up` 与
`enterprise_vector_sync_jobs{status="queued|succeeded|failed"}`；告警规则覆盖服务不可用和重试耗尽。

该迁移不提供自动 downgrade：恢复旧实现需要同时恢复旧代码与迁移前 PostgreSQL 备份。
不要把已迁移数据库直接交给旧分支运行。现有 Git 历史仍可查看迁移前实现。

## 本次迁移记录

2026-10-13 在 `codex/migrate-to-milvus` 分支完成本地部署：

- 54 份文档、四个部门范围、470 个 1024 维向量，54 个导入任务全部成功。
- 逐条校验 Milvus 中的 float32 向量值与迁移载荷相同，470 个分块 ID、正文哈希、范围及关键词哈希保持不变。
- SQL 已无 embedding 列，导入队列无待处理或失败任务，迁移调用嵌入模型次数为 0。
- 54 份文档各抽取一个原有向量，真实检索均召回原分块；同时验证单部门、多知识库及空范围。
- API、Worker 和 Milvus 健康检查通过，无认证问答请求返回 401。
- 751 项 Python 测试全部通过（包含真实 PostgreSQL、Milvus 和 Redis 集成测试），19 项前端测试通过。

本地备份与完整校验结果位于被 Git 忽略的 `data/ingestion-reports/milvus-migration/`：
`postgres-before.dump`、`before.json`、`vector-import.json`、`verification.json`、`retrieval-probes.json`。
这些文件含实际项目数据，仅供本地恢复与核对。
上述结果证明数据与检索链路可用，不是新的完整 80 题答案质量评估，也不构成规模化性能结论。
