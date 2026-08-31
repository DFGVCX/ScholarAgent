# ScholarAgent 中文启动说明

> 本文用于在本地或 Docker 环境启动 ScholarAgent。Docker 环境的主要访问入口是：`http://127.0.0.1:3000`。

## 1. 启动前确认

请先进入项目根目录：

```powershell
cd E:\code\ScholarAgent
```

确认当前目录下存在这些文件和目录：

```text
app/
agents/
skills/
mcp_server/
frontend/
requirements.txt
docker-compose.yml
```

## 2. 本地快速启动

适合开发、调试和查看前端页面。

### 2.1 创建并激活虚拟环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

如果你的系统中 `python` 不可用，可以先查看可用版本：

```powershell
py -0p
```

然后用指定版本创建虚拟环境，例如：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2.2 安装依赖

```powershell
pip install -r requirements.txt
```

当前核心依赖包括 FastAPI、Uvicorn、SQLAlchemy、psycopg、pgvector、Redis 客户端、aiohttp、pypdf 等。

### 2.3 启动 Browser Worker

需要 WebVPN、EZproxy、出版社登录或知网自动下载时，先启动独立可见浏览器服务：

```powershell
.\scripts\start_browser_worker.ps1
```

健康检查：`http://127.0.0.1:8002/health`。Worker 会复用本机 Microsoft Edge，学校账号、验证码和二次认证由用户在弹出的浏览器中完成。

### 2.4 启动标准 MCP Server

```powershell
.\.venv\Scripts\python.exe mcp_server\server.py --transport streamable-http --host 127.0.0.1 --port 8001
```

### 2.5 启动后端

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

启动成功后访问：

```text
健康检查：http://127.0.0.1:8000/health
基础设施检查：http://127.0.0.1:8000/health/infra
前端控制台：http://127.0.0.1:8000/app.html
```

## 3. 默认登录账号

开发环境默认保留两个演示账号：

| 租户 | 用户名 | 密码 | API Key |
|---|---|---|---|
| `tenant_demo` | `demo` | `demo123` | `demo-key` |
| `tenant_acme` | `acme` | `acme123` | `acme-key` |

推荐先使用：

```text
租户：tenant_demo
用户名：demo
密码：demo123
```

登录后进入：

- 智能会话
- 写作专项
- 任务列表 / 引用审计
- 个人知识库
- 个人中心

## 4. PostgreSQL/pgvector 与 Redis

项目使用 PostgreSQL 作为唯一关系数据与检索事实源，不提供 SQLite、JSON 或 Chroma 回退。

- PostgreSQL/pgvector 保存任务、会话、论文、切片、向量、事件和审计数据。
- PostgreSQL 不可用时后端健康检查失败，不写入其他本地数据库。
- Redis 可用时：用于限流和任务事件流增强。
- Redis 不可用时：会回退到内存实现，但重启后内存状态会丢失。

### 4.1 推荐环境变量

可以复制 `.env.example` 中的配置，或在 PowerShell 中临时设置：

```powershell
$env:SCHOLAR_DATABASE_URL="postgresql+psycopg://scholar:scholar@127.0.0.1:5432/scholar_agent"
$env:SCHOLAR_REDIS_URL="redis://127.0.0.1:6379/0"
$env:SCHOLAR_STORAGE_BACKEND="postgresql"
$env:SCHOLAR_ALLOW_MOCK_DATA="false"
$env:SCHOLAR_EXTERNAL_SOURCE_PROVIDER="real"
```

### 4.2 初始化 PostgreSQL

启动 PostgreSQL/pgvector 后，使用 Alembic 创建表结构：

```powershell
$env:SCHOLAR_DATABASE_URL="postgresql+psycopg://scholar:scholar@127.0.0.1:5432/scholar_agent"
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe scripts\init_infra.py
```

初始化会创建或校验以下核心表：

- `scholar_tenants`
- `scholar_users`
- `scholar_tasks`
- `scholar_conversations`
- `scholar_conversation_messages`
- `scholar_knowledge_papers`
- `scholar_rag_chunks`
- `scholar_task_events`
- `scholar_citation_audits`
- `scholar_reflection_logs`
- `scholar_user_preferences`
- `scholar_trace_events`

### 4.3 通过网页配置模型

数据库由 Docker Compose 和 Alembic 初始化；网页不接收数据库管理员凭据。模型配置流程如下：

1. 打开 `http://127.0.0.1:3000`。
2. 使用 `demo / demo123 / tenant_demo` 登录。
3. 进入“个人中心”。
4. 在“模型路由”填写 Agent provider、Base URL、API Key 和模型名，先点“测试 Agent 模型”。
5. 在“知识检索”填写千问 Embedding Base URL、API Key 和模型名，先点“测试 Embedding”。
6. 测试通过后保存。API Key 输入框留空会保留服务端现有密钥，不会回显明文。
7. 更换 Embedding 模型或服务地址后，点击“重新生成向量”并观察 ready/stale/failed/pending 计数。

运行配置按部署保存到 PostgreSQL：

```text
scholar_settings
```

## 5. 模型配置

写作专项和智能会话需要真实模型时，至少配置主模型 provider、Base URL、API Key 和模型名。

OpenAI-compatible 类型示例：

```powershell
$env:SCHOLAR_PRIMARY_MODEL_PROVIDER="openai-compatible"
$env:SCHOLAR_LLM_BASE_URL="https://你的模型服务地址"
$env:SCHOLAR_LLM_API_KEY="你的模型密钥"
$env:SCHOLAR_LLM_MODEL="你的模型名称"
```

如果使用阿里云百炼、硅基流动、OneAPI、LiteLLM、自建 vLLM 等 OpenAI-compatible 网关，通常都走上述配置。请不要把真实密钥提交到 Git。

配置完成后，可以在前端“个人中心”使用模型探测，也可以调用：

```http
POST /settings/model/probe
Header: X-API-Key: demo-key
```

## 6. RAG 配置

RAG 固定使用 PostgreSQL 全文检索和 pgvector，向量服务固定为千问兼容接口：

```powershell
$env:SCHOLAR_RAG_INDEX_BACKEND="pgvector"
$env:SCHOLAR_RAG_RETRIEVAL_MODE="hybrid_rrf"
$env:SCHOLAR_RAG_EMBEDDING_PROVIDER="qwen"
```

配置千问 Embedding：

```powershell
$env:SCHOLAR_RAG_RETRIEVAL_MODE="hybrid_rrf"
$env:SCHOLAR_RAG_EMBEDDING_PROVIDER="qwen"
$env:SCHOLAR_RAG_EMBEDDING_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode"
$env:SCHOLAR_RAG_EMBEDDING_API_KEY="你的embedding密钥"
$env:SCHOLAR_RAG_EMBEDDING_MODEL="qwen3.7-text-embedding"
$env:SCHOLAR_RAG_EMBEDDING_DIMENSIONS="1024"
$env:SCHOLAR_RAG_SEMANTIC_TIMEOUT_SECONDS="8"
$env:SCHOLAR_RAG_MAX_CHUNKS_PER_PAPER="3"
```

`SCHOLAR_RAG_SEMANTIC_TIMEOUT_SECONDS` 是单次交互检索中“查询 Embedding + pgvector 候选”的总时间预算。超时会取消语义链路，保留已经完成的 PostgreSQL lexical 结果，并在响应 `warnings` 中写明降级原因；它不影响后台论文入库的 Embedding 批处理超时。

`SCHOLAR_RAG_MAX_CHUNKS_PER_PAPER` 控制 RRF 后的首轮论文多样性，默认每篇最多占 3 个位置。若没有足够的其他论文候选，系统会按原 RRF 顺序回填同篇的其他 Chunk，尽量保持请求的 Top-K 数量；设为 `0` 可关闭限制。响应中的 `ranking_policy` 会回显实际策略。

检索后处理还会抑制同论文中高度重叠的相邻 prose Chunk，但只有共享来源块，或同章节且位置相邻时才进行相似度判断。表格与算法分片不会做模糊去重，避免把重复表头/标题下的不同数据行或步骤误删；阈值和适用范围可在响应 `ranking_policy` 中审计。

常用检查接口：

```text
GET http://127.0.0.1:8000/knowledge/rag/stats
GET http://127.0.0.1:8000/knowledge/rag/search?query=citation&limit=5
```

`rag/stats` 除论文和 Chunk 数外，还返回 `vector_count`、`failed_jobs`、`pending_jobs`、`chunk_table_bytes`、`chunk_index_bytes`。以下三个 ready 向量一致性指标应长期为 0：

- `ready_noncurrent_chunks`：仍标记 ready、但已经不属于论文当前内容版本；
- `ready_missing_vectors`：标记 ready、实际向量为空；
- `ready_wrong_model`：标记 ready、但模型不是当前配置模型。

任一指标非 0 时，`consistency_status` 为 `degraded`，`consistency_error_count` 返回异常总数。

本地 lexical 与 pgvector 候选支持相同的结构化过滤参数；多值参数可重复传入：

```text
GET http://127.0.0.1:8000/knowledge/rag/search?query=robust+aggregation&year_from=2020&year_to=2024&author=Alice&venue=NeurIPS&section=method&chunk_type=equation&chunk_type=table
```

可用参数为 `paper_id`、`year_from`、`year_to`、`author`、`venue`、`section` 和 `chunk_type`。对象类型支持 `prose/equation/table/figure/algorithm/code`。响应中的 `filters` 是服务实际采用的规范化条件。

lexical 检索会把常见学术概念做中英术语和缩写的双向扩展，例如 `联邦学习 ↔ federated learning ↔ FL`、`差分隐私 ↔ differential privacy ↔ DP`。短英文缩写按完整单词匹配，不会在 `workflow` 等普通单词内部误触发。响应中的 `query_expansions` 会列出本次实际加入的词面查询，Embedding 不可用时也能调试中文兜底召回。

每个本地命中还包含 `citation`，其中稳定 key 由 `paper_id + content_version + chunk_id` 组成，并附页码范围和章节路径。论文重新解析产生新内容版本后，旧 key 不会静默漂移到新 Chunk；最终回答是否采用该证据由 Agent 层决定。

当本次 Top-K 中存在同论文、同内容版本、同章节且 `chunk_index` 连续的命中时，响应还会返回 `merged_contexts`。它不会替换或重排 `local_hits`，只按论文顺序拼接完整原文，并保留组成它的 `chunk_ids`、`chunk_types` 和逐 Chunk `citation_keys`。MCP/Agent 调用链按 `chunk_id` 保留多个同论文命中，不再退化成“一篇论文只留一个 Chunk”。

检索结果中的 `previous_chunk_id` / `next_chunk_id` 可用于按需展开完整上下文。接口只返回当前用户知识库、当前论文内容版本中的 Chunk，并且只按完整 Chunk 控制 token 预算，不截断原文：

```text
GET http://127.0.0.1:8000/knowledge/rag/chunks/{chunk_id}/context?before=2&after=2&token_budget=2048
GET http://127.0.0.1:8000/knowledge/rag/chunks/{chunk_id}/parent
```

当中心 Chunk 自身超过预算时，响应仍保留中心完整原文，并返回 `budget_exceeded=true`；`truncated=true` 表示部分请求的相邻 Chunk 因预算未被选入。

`parent` 接口用于父子检索：嵌套小节命中时优先返回父章节，否则返回当前章节。它返回完整章节原文与章节/页码 provenance，不会自动附加到每次 Top-K 检索结果。

调用时需要携带：

```http
X-API-Key: demo-key
```

## 7. 外部论文源

写作专项会通过 MCP 工具检索：

- OpenAlex
- arXiv
- Crossref
- 当前租户个人知识库

相关配置：

```powershell
$env:SCHOLAR_EXTERNAL_SOURCE_PROVIDER="real"
$env:SCHOLAR_EXTERNAL_SOURCE_TIMEOUT_SECONDS="8"
```

如果 OpenAlex、arXiv 或 Crossref 访问失败，常见原因包括：

- 本机网络或代理阻断。
- 目标站点临时不可用。
- 公司网络禁止访问外部论文源。
- 超时时间过短。

这种情况下可以先在“个人知识库”上传或保存论文，再进入写作专项生成。

## 8. Docker 启动

如果你希望用容器启动 PostgreSQL/pgvector、Redis、后端和前端，可以使用 Docker Compose。

### 8.1 启动完整网站

如果要使用 `scholar_hierarchical_v4`，先执行一次 Docling 模型准备命令：

```powershell
docker compose --profile setup run --rm --build docling_models
```

它只下载当前文本型论文流程需要的 layout、TableFormer 和 code/formula 模型，不下载 OCR 模型。模型保存在 `scholar_storage` 卷的 `/app/storage/models/docling`，backend 和 worker 会显式从这个目录加载，后续重启不需要重复下载。

命令结束时会输出 JSON。只有 `ready: true`、`runtime.cpu_only: true` 且 `missing` 为空，才表示模型准备完成。以后可以不下载、只复检：

```powershell
docker compose --profile setup run --rm docling_models `
  python -m app.papers.docling_models check `
  --output-dir /app/storage/models/docling
```

模型准备完成后启动网站：

```powershell
docker compose up -d --build
```

访问：

```text
前端：http://127.0.0.1:3000
后端：http://127.0.0.1:8000
```

前端宿主机端口可通过 `.env` 中的 `SCHOLAR_FRONTEND_PORT` 修改；默认使用 `3000`，以避免和本机已有的 nginx/IIS 服务争用端口 `80`。

### 8.2 查看状态和日志

```powershell
docker compose ps
docker compose logs -f backend frontend worker
```

Compose 会先运行 migration，再启动后端、worker、MCP、browser worker 和前端。`docling_models` 属于 `setup` profile，不会在普通启动时重复执行。网页运行配置会被服务动态读取；修改 `.env` 后则需要执行 `docker compose up -d --force-recreate`。

## 9. 常用验证命令

### 9.1 检查后端是否启动

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health
```

预期返回：

```json
{"status":"ok","service":"scholar-agent"}
```

### 9.2 检查基础设施

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health/infra
```

重点看：

- `database.available`
- `database.pgvector`
- `redis.available`
- `runtime_backend.storage`
- `runtime_backend.rag`
- `model.configured`
- `external_sources.provider`

### 9.3 检查任务列表

```powershell
Invoke-WebRequest -UseBasicParsing `
  -Uri "http://127.0.0.1:8000/tasks" `
  -Headers @{ "X-API-Key" = "demo-key" }
```

### 9.4 检查知识库

```powershell
Invoke-WebRequest -UseBasicParsing `
  -Uri "http://127.0.0.1:8000/knowledge?query=&source=local&limit=20" `
  -Headers @{ "X-API-Key" = "demo-key" }
```

## 10. 常见问题

### 10.1 页面打不开

确认后端是否启动：

```text
http://127.0.0.1:8000/health
```

当前 active 前端是：

```text
frontend/dist/app.html
```

不要打开旧的根目录 Demo；旧文件已经归档到 `archive/legacy-root-index.html`。

### 10.2 登录提示 Invalid username, password, or tenant

优先使用：

```text
tenant_demo / demo / demo123
```

检查 `scholar_users` 和 `scholar_tenants` 是否初始化成功。也可以重新执行：

```powershell
.\.venv\Scripts\python.exe scripts\init_infra.py
```

### 10.3 写作任务失败

先看 `/health/infra`：

- 模型是否已配置。
- 外部论文源是否可访问。
- 当前租户知识库是否有论文。
- PostgreSQL/pgvector 是否可用。

如果外部论文源不可用，可以先在“个人知识库”上传论文，再提交写作专项。

### 10.4 Redis 不可用

Redis 不可用时系统会退回内存限流和事件队列。开发阶段可以继续使用，但重启后状态会丢失。需要稳定运行时请启动 Redis。

### 10.5 PostgreSQL 不可用

系统不会回退到本地文件数据库。检查连接串并确认 migration 已执行：

```text
SCHOLAR_DATABASE_URL=postgresql+psycopg://用户:密码@主机:5432/数据库
```

### 10.6 模型探测失败

检查：

- `SCHOLAR_PRIMARY_MODEL_PROVIDER`
- `SCHOLAR_LLM_BASE_URL`
- `SCHOLAR_LLM_API_KEY`
- `SCHOLAR_LLM_MODEL`
- 网络代理或防火墙
- 模型服务是否兼容 OpenAI Chat Completions 接口

### 10.7 Docker Desktop 在 `dockerInference` 启动阶段崩溃

如果 Docker Desktop 日志包含以下错误：

```text
initializing Inference manager ... remove ...\Docker\run\dockerInference:
The file cannot be accessed by the system. (error 1920)
```

这是用户目录里的 Docker 运行时 socket 残留，不是 ScholarAgent 镜像或数据卷损坏。完全退出 Docker Desktop 后，只清理该 socket，再启动 Docker：

```powershell
$socket = Join-Path $env:LOCALAPPDATA "Docker\run\dockerInference"
Remove-Item -LiteralPath $socket -Force
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
```

### 10.8 Windows Python 3.14 与 async psycopg

Python 3.14 在 Windows 默认创建 `ProactorEventLoop`，而 psycopg 的异步连接要求 selector-based loop。项目会在导入数据库运行时、创建 Uvicorn 或 unittest 事件循环之前切换为 `WindowsSelectorEventLoopPolicy`，因此不应再出现：

```text
Psycopg cannot use the 'ProactorEventLoop' to run in async mode
```

可单独验证：

```powershell
python -m unittest tests.test_db_event_loop
```

该兼容层只在 Windows 生效。Python 3.16 计划移除 event-loop policy API，届时需要在进程宿主改用显式 `loop_factory`；代码已将兼容逻辑隔离在 `app/db/session.py`，便于替换。

不要删除 `Docker\wsl`、Docker data、`scholar_storage` 或 `scholar_postgres`。如果当前终端无法访问该 ReparsePoint，需要在有相应权限的 PowerShell 中执行清理。

## 11. 推荐开发启动流程

日常开发建议使用这个顺序：

1. 启动 PostgreSQL/pgvector 和 Redis。
2. 激活 `.venv`。
3. 执行 `python -m alembic upgrade head` 和 `scripts\init_infra.py`。
4. 启动 `uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`。
5. 打开 `http://127.0.0.1:3000`（本地仅启动后端时也可使用 `http://127.0.0.1:8000/app.html`）。
6. 登录 `tenant_demo / demo / demo123`。
7. 在个人中心检查模型、数据库、RAG。
8. 在个人知识库上传或保存论文。
9. 在写作专项提交任务。

## 12. 相关文档

- [架构学习导读](../ARCHITECTURE_LEARNING.md)
- [项目结构规范](../PROJECT_STRUCTURE.md)
- [扩展契约](../EXTENSION_CONTRACT.md)
- [PostgreSQL / Redis / RAG 初始化](../../deploy/README.md)
- [部署资产说明](../../deploy/README.md)
