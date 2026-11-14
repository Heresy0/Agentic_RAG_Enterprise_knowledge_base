# 8000统一工作台

入口提供三个工作区：`/`通用知识、`/erp/` ERP、`/mes/` MES。三个后端使用同一代码镜像，各自保留数据库、资料和授权；Caddy只发布`127.0.0.1:8000`，API与Worker不发布主机端口。前端API、登录回调、退出地址和PKCE状态遵循工作区前缀。

## 可移植模板

[`compose.yaml`](compose.yaml)是应用层模板，供**新目标机器**配置使用，不替换本机正在使用的私有Compose。要求Docker Compose 2.30或以上（本轮使用2.31验证），使用[`env_file.format: raw`](https://docs.docker.com/reference/compose-file/services/#format)，密码中的`$`不会被二次插值。模板没有凭据、机器绝对路径、固定容器名或固定子网。

| 文件 | 复制后用途 |
| --- | --- |
| [.env.example](.env.example) | Compose项目名、镜像、私有目录、外部网络和资料卷名称，保存为私有`compose.env` |
| [common.env.example](common.env.example) | 共享模型配置与OIDC，API密钥留空，保存为`common.env` |
| [knowledge.env.example](knowledge.env.example) | 通用库连接、Redis及原Milvus集合，保存为`knowledge.env` |
| [erp.env.example](erp.env.example) | ERP库、Redis、集合及只读适配器令牌，保存为`erp.env` |
| [mes.env.example](mes.env.example) | MES库、Redis、集合及只读适配器令牌，保存为`mes.env` |

服务先读取`common.env`，再读取自己的工作区文件；工作区值覆盖共享值。挂载位置、子路径、导航开关由Compose固定，数据库和凭据不写入YAML。默认启动网关及三个API；`workers` profile额外启用通用库索引与质量Worker，与现有统一部署的范围一致。ERP/MES API保留字段问答、工序/卡、批次/步骤、映射和历史接口；它们的独立后台任务若需执行，应另按对应数据库配置Worker，不能让通用Worker处理三库。

## 新机器准备

先按[基础部署](../../README.md)、[ERPNext接入](../../docs/ERPNEXT_INTEGRATION.md)、[OpenMES接入](../../docs/OPENMES_INTEGRATION.md)准备依赖。此模板不创建或初始化以下资源，也不在启动时迁移、重建索引或导入评测夹具。

- PostgreSQL须具备项目所需的pg_search等依赖，三个工作区分别恢复原数据库；保留租户、用户、知识库、业务授权、映射、会话、快照和账本的原ID。
- 三个原件卷分别恢复到目标Docker volume，填写其真实名称。卷声明为`external`，避免用自动创建的空卷代替已有资料。
- 一致地恢复Milvus集合、向量及服务账号权限；完整迁移还需对应etcd/MinIO状态。SQL分块和向量需对应，不能用空索引冒充恢复成功。
- 恢复Keycloak realm、用户身份及OIDC配置，保留用户`sub`与平台用户映射；仅重建同名账号不能恢复原权限。保留ERPNext/OpenMES上游数据、只读适配器和源身份。
- 准备一个可访问上述依赖的外部Docker网络，填写`UNIFIED_DEPENDENCY_NETWORK`。将PostgreSQL、各Redis、Milvus、Keycloak和两个适配器接入该网络，使用可解析的容器DNS；容器里的`127.0.0.1`不能指向宿主依赖。网关另连普通边缘网络，API通过依赖网络访问模型供应商；不要把依赖网络配置为阻断外网的内部网络。

以下命令均在**新机器的仓库根目录**执行。新建私有目录并复制空值示例；实际密钥、连接串和源配置只保存在被忽略的`tmp/`：

```powershell
New-Item -ItemType Directory -Path tmp/unified-workspace-portable
Copy-Item deploy/unified_workspace/.env.example tmp/unified-workspace-portable/compose.env
Copy-Item deploy/unified_workspace/common.env.example tmp/unified-workspace-portable/common.env
Copy-Item deploy/unified_workspace/knowledge.env.example tmp/unified-workspace-portable/knowledge.env
Copy-Item deploy/unified_workspace/erp.env.example tmp/unified-workspace-portable/erp.env
Copy-Item deploy/unified_workspace/mes.env.example tmp/unified-workspace-portable/mes.env
```

补齐`DATABASE_URL`、原集合名、供应商配置、API密钥和适配器只读令牌；三个数据库不能合并。不同工作区可使用各自的模型和Milvus地址覆盖共享配置。示例Redis DNS也应按目标依赖修改。`UNIFIED_PRIVATE_DIR`相对`deploy/unified_workspace/`解析，默认指向上述私有目录；也可填新机器上的绝对路径，Windows建议正斜杠。

服务env文件使用原样值，不给值额外套引号；连接串中的URL特殊字符仍需按URL规则编码。Compose插值文件`compose.env`采用通常的Compose语法。

私有目录还需准备：

| 文件 | 内容 |
| --- | --- |
| `knowledge-api.token`、`erp-api.token`、`mes-api.token` | 对应Milvus只读账号的`username:password`，由Compose secrets只读挂载 |
| `knowledge-worker.token` | 通用库索引Worker的Milvus写账号；启用Worker时使用 |
| `erp-sources.json`、`mes-sources.json` | 恢复的源配置，分别使用`execution_selection`与`batch_step_selection` |
| `erp-evaluation-registry.json`、`mes-evaluation-registry.json` | 恢复的源注册记录，供现有质量审核/评测链路核验 |

源配置和注册结构见[ERP源示例](../../config/erpnext_sources.example.json)、[ERP注册示例](../../config/erpnext_evaluation_registry.example.json)、[MES源示例](../../config/openmes_sources.example.json)、[MES注册示例](../../config/openmes_evaluation_registry.example.json)。示例中的租户ID和全零摘要是占位符，须替换为恢复数据中的真实值；适配器地址、`token_env`、只读令牌及源描述摘要须匹配。地址或源修订变化应走既有核验/审核流程，不回填旧快照。Linux上须让镜像中的非root应用用户能读取挂载的凭据文件，文件型Compose secrets不会自动替你调整宿主文件所有者。

OIDC公开客户端使用PKCE S256，配置精确回调及退出地址：

```text
http://127.0.0.1:8000/
http://127.0.0.1:8000/erp/
http://127.0.0.1:8000/mes/
```

浏览器使用的issuer和容器可达的JWKS地址分别填写。网关不读取模型或数据库凭据；模板不包含Keycloak管理员凭据。

## 检查、构建和启动

只检查配置，不连接依赖或启动容器：

```powershell
docker compose --env-file tmp/unified-workspace-portable/compose.env -f deploy/unified_workspace/compose.yaml --profile workers config --quiet
```

实际env文件会进入解析结果，使用`--quiet`避免把凭据打印到终端或报告。配置检查不验证数据库、向量、源身份或文件访问权限，不能代替目标机器验收。

默认通过仓库根`Dockerfile`构建全部API/Worker共用镜像，**不依赖本机旧零模型基础镜像**。也可载入已归档镜像并在`UNIFIED_IMAGE`指定其标签或摘要；严格复现时记录应用和Caddy镜像的摘要，使用已归档镜像时跳过构建。

```powershell
docker compose --env-file tmp/unified-workspace-portable/compose.env -f deploy/unified_workspace/compose.yaml build knowledge
```

恢复目标库并完成备份核对后，按目标库的实际版本显式执行所需迁移；命令只应作用于新目标库，不回灌或降级现有库：

```powershell
docker compose --env-file tmp/unified-workspace-portable/compose.env -f deploy/unified_workspace/compose.yaml run --rm --no-deps knowledge python -m alembic upgrade head
docker compose --env-file tmp/unified-workspace-portable/compose.env -f deploy/unified_workspace/compose.yaml run --rm --no-deps erp python -m alembic upgrade head
docker compose --env-file tmp/unified-workspace-portable/compose.env -f deploy/unified_workspace/compose.yaml run --rm --no-deps mes python -m alembic upgrade head
docker compose --env-file tmp/unified-workspace-portable/compose.env -f deploy/unified_workspace/compose.yaml up -d --pull never
```

确认三个工作区健康、登录回调、ERP工序/卡、MES批次/步骤和原历史可读后，再按需要启动Worker：

```powershell
docker compose --env-file tmp/unified-workspace-portable/compose.env -f deploy/unified_workspace/compose.yaml --profile workers up -d --pull never
```

质量Worker使用现有`--allow-model-calls`模式，会处理已排队且有预算授权的任务。迁移时先核对旧队列再启用，不为验证模板新建评测任务。API仍可按用户提问调用模型；模板不自动发问、索引或运行黄金题。

## 本机现有部署

本机已验收的统一部署使用源码`a995666`构建的同一镜像，后续main合并未重新部署。原三库、资料、权限、历史及旧容器备份保留，8005/8006停止常驻。既有环境仍使用忽略目录`tmp/unified-workspace-8000-20261114-r1/compose.json`，**本次不切换为上述模板**。

同目录的[`Dockerfile`](Dockerfile)保留了当时复用本机依赖镜像的离线构建方式；它是历史部署路径，不是新机器的构建前提。原备份、启动及回退范围见[统一部署验收](../../docs/reports/unified-workspace-8000-20261114-r1.md)。本次模板只做离线配置检查，没有启动新容器、修改原配置、恢复数据或产生新的模型成绩。
