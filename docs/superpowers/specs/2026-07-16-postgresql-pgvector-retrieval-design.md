# PostgreSQL + pgvector 论文存储与统一检索设计

## 1. 文档状态

本设计用于 ScholarAgent 新分支 `refactor/postgres-pgvector-retrieval`。它覆盖论文数据模型、PostgreSQL 存储迁移、论文入库生命周期、统一检索接口、多租户隔离、故障恢复、测试与部署。

已确认的决策：

- 使用全新的 PostgreSQL 数据库，不迁移现有 SQLite、JSON 或 Chroma 数据。
- PostgreSQL 是唯一关系数据和检索事实源，不提供 SQLite、JSON 或 Chroma 运行时回退。
- 使用 pgvector 存储和检索向量。
- embedding 固定为 `Qwen3-Embedding-0.6B`、1024 维、归一化向量和 cosine distance。
- PDF 等原始文件保存在持久化文件卷；PostgreSQL 保存文件 URI、哈希、大小、格式和处理状态。
- 本阶段不引入 reranker，不改造论文阅读器交互，不重构 Agent 主循环；只切换这些消费者使用统一检索契约。

## 2. 目标

1. 消除当前 SQLite、JSON、Chroma 和文档中 MySQL 说明之间的多重事实源。
2. 保证论文元数据、正文版本、切片和向量的一致生命周期。
3. 在数据库约束和应用仓储两层保证租户与用户隔离。
4. 为 MCP、API、会话 Agent 和写作流程提供同一个检索入口和结果结构。
5. 将外部搜索候选与本地可引用证据严格分开。
6. 支持词法检索、语义检索和可解释的混合检索。
7. 在解析或 embedding 服务故障时保留可恢复状态，不静默丢数据或回退到另一套数据库。

## 3. 非目标

- 不迁移旧数据。
- 不保留 MySQL 兼容层。
- 不继续维护 `knowledge.json`、`rag_chunks.json` 或 Chroma collection。
- 不在本阶段加入 Qwen Reranker。
- 不把 PDF 二进制直接存入 PostgreSQL。
- 不在本阶段完成阅读器的坐标高亮、目录、书签或历史翻译 UI。
- 不改变外部论文源的抓取协议，只改变候选持久化和入库边界。

## 4. 总体架构

```text
API / MCP / Agent / Writing Skill
              |
              v
       RetrievalService -------- ExternalPaperSearch
              |
              v
      PaperRepository / JobRepository
              |
              v
       PostgreSQL + pgvector
              ^
              |
       PaperIngestionService
              ^
              |
上传 / arXiv / DOI / OpenAlex / 知网 / 机构下载
```

### 4.1 数据库基础层

新增 `app/db/`，负责异步连接池、事务、会话上下文、模型和 Alembic migration。实现采用 SQLAlchemy 2 异步接口、psycopg 3 和 Alembic；业务代码不直接持有连接或执行未封装 SQL。

所有现有关系型运行表也迁移到 PostgreSQL，包括用户、任务、会话、工具调用、记忆、运行配置、追踪、批注和翻译。论文领域在本阶段获得独立 Repository；其他领域可以暂时保留现有 service API，但底层必须切换到 PostgreSQL，不能继续访问 SQLite。

生产启动不自动建表。部署流程先执行 `alembic upgrade head`，再启动 backend、worker 和 MCP。健康检查必须验证数据库连接、`vector` 扩展和关键 migration revision。

### 4.2 论文领域层

- `PaperRepository`：论文、资产、正文版本、切片和知识库状态的事务操作。
- `PaperIngestionService`：文件校验、去重、解析、切片和 embedding 任务编排。
- `PaperJobRepository`：持久化下载、解析、切片、embedding 和重建任务。
- `RetrievalService`：唯一的本地与外部检索入口。
- `ExternalPaperSearch`：OpenAlex、arXiv、Crossref、知网等元数据候选检索；不直接写库。

## 5. 数据模型

### 5.1 `papers`

保存论文身份、规范化元数据、知识库状态和当前正文版本。

关键字段：

```text
paper_uuid UUID PRIMARY KEY
tenant_id VARCHAR(64) NOT NULL
user_id VARCHAR(64) NOT NULL
paper_id VARCHAR(260) NOT NULL
source VARCHAR(40) NOT NULL
source_identifier VARCHAR(300)
normalized_doi VARCHAR(300)
normalized_arxiv_id VARCHAR(120)
title TEXT NOT NULL
authors JSONB NOT NULL DEFAULT '[]'
abstract TEXT NOT NULL DEFAULT ''
published_at TIMESTAMPTZ
canonical_url TEXT
in_knowledge_base BOOLEAN NOT NULL DEFAULT TRUE
ingestion_status VARCHAR(32) NOT NULL
current_content_version INTEGER NOT NULL DEFAULT 0
last_error TEXT
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
deleted_at TIMESTAMPTZ
```

约束：

- `(tenant_id, user_id, paper_id)` 唯一。
- `(tenant_id, user_id, normalized_doi)` 在 DOI 非空时唯一。
- `(tenant_id, user_id, normalized_arxiv_id)` 在 arXiv ID 非空时唯一。
- 标题相同不自动判定为同一论文。
- `ingestion_status` 只允许 `metadata_only`、`acquiring`、`parsing`、`embedding`、`ready`、`failed`。

### 5.2 `paper_assets`

保存原始 PDF 或其他可解析文件的信息，不保存二进制。

关键字段：`asset_uuid`、租户与用户、`paper_uuid`、`asset_kind`、`file_uri`、`file_name`、`mime_type`、`sha256`、`file_size`、`page_count`、`validation_status`、`parser_name`、`parser_version`、创建时间。

同一用户范围内 `sha256` 唯一。所有子表使用包含 `tenant_id`、`user_id` 和 `paper_uuid` 的复合外键，数据库层禁止把资产关联到其他租户或用户的论文。

### 5.3 `paper_contents`

保存每个正文版本的完整文本和解析审计信息。

关键字段：`content_uuid`、租户与用户、`paper_uuid`、`content_version`、`full_text`、`content_hash`、`language`、`extraction_method`、`extraction_quality`、创建时间。

`(paper_uuid, content_version)` 唯一。正文更新时只新增版本，不原地覆盖历史正文；检索只使用 `papers.current_content_version` 指向的版本。

### 5.4 `paper_chunks`

保存检索切片、位置锚点、词法索引和向量。

关键字段：

```text
chunk_uuid UUID PRIMARY KEY
tenant_id VARCHAR(64) NOT NULL
user_id VARCHAR(64) NOT NULL
paper_uuid UUID NOT NULL
content_uuid UUID NOT NULL
content_version INTEGER NOT NULL
chunk_index INTEGER NOT NULL
page_start INTEGER
page_end INTEGER
char_start INTEGER
char_end INTEGER
section_title TEXT
content TEXT NOT NULL
content_hash CHAR(64) NOT NULL
lexical_tokens TEXT NOT NULL
search_vector TSVECTOR GENERATED ALWAYS AS (...)
embedding VECTOR(1024)
embedding_model VARCHAR(160)
embedding_status VARCHAR(24) NOT NULL
embedding_updated_at TIMESTAMPTZ
created_at TIMESTAMPTZ NOT NULL
```

索引：

- `(tenant_id, user_id, paper_uuid)` B-tree。
- `(paper_uuid, content_version, chunk_index)` 唯一。
- `search_vector` GIN。
- 非空 `embedding` 上的 HNSW `vector_cosine_ops`。
- `embedding_status` 只允许 `pending`、`running`、`ready`、`failed`。

应用沿用当前中英文 tokenizer：英文和数字按词切分，连续中文生成双字词。生成的空格分隔 token 写入 `lexical_tokens`，PostgreSQL 使用 `simple` 配置生成 `tsvector`，避免依赖额外中文分词扩展。

### 5.5 `paper_ingestion_jobs`

保存可重试任务，字段包括 `job_uuid`、租户与用户、`paper_uuid`、任务类型、状态、attempt、幂等键、payload、错误、下次执行时间和时间戳。

任务类型包括 `download`、`parse`、`chunk`、`embed`、`rebuild`、`purge_file`。状态包括 `pending`、`running`、`succeeded`、`failed`、`cancelled`。默认最多尝试 3 次，使用指数退避。Worker 通过 `FOR UPDATE SKIP LOCKED` 领取任务，避免同一任务被并发执行。

### 5.6 阅读相关表

`paper_annotations` 和 `paper_translations` 改用 `paper_uuid` 复合外键。批注保留 page、类型、颜色、位置点和内容；翻译保留原文哈希、源语言、目标语言、译文、provider 和 model。本阶段只迁移存储模型与现有接口，不扩展阅读器交互。

## 6. 多租户隔离

论文相关表全部包含 `tenant_id` 和 `user_id`，并通过复合外键保证关系一致。所有 Repository 方法必须显式接收 `UserContext`，不提供缺少租户上下文的公共查询方法。

论文表启用 PostgreSQL Row Level Security：

- API 事务使用 `SET LOCAL` 写入当前 tenant 和 user 设置。
- RLS policy 只允许读取和修改当前 tenant/user 的行。
- 普通应用角色不能绕过 RLS。
- 后台 worker 使用独立受控角色领取跨租户任务；每次任务操作仍校验任务携带的 tenant/user，并记录审计事件。
- migration 使用独立 owner 角色，应用账号不拥有 DDL 权限。

## 7. 入库生命周期与事务边界

### 7.1 外部候选

外部检索结果仅存在于 `RetrievalResult.external_candidates`，不写入数据库。只有用户明确保存、下载成功，或上传文件后，系统才创建本地论文。

### 7.2 文件写入

1. 下载或上传到租户 staging 目录。
2. 校验文件大小、类型、PDF 签名和 SHA256。
3. 使用 SHA256 检查用户范围内重复资产。
4. 原子移动到正式文件目录。
5. PostgreSQL 事务写入 `papers`、`paper_assets` 和解析任务。
6. 若数据库事务失败，补偿删除刚移动的文件；定期 orphan sweeper 清理超过 24 小时且无数据库记录的 staging/正式孤儿文件。

### 7.3 解析、切片与 embedding

1. 解析 worker 生成完整正文和页级文本。
2. 在同一个事务中创建新 `paper_contents` 版本和全部 `paper_chunks`，chunk embedding 初始为空。
3. 创建 embedding 任务并将论文状态设为 `embedding`。
4. embedding worker 分批调用 Qwen3-Embedding-0.6B，校验每个结果为 1024 维有限数值并完成归一化。
5. 在事务中更新 chunk 向量和状态。
6. 当前版本全部 chunk 完成后，将论文状态设为 `ready`。

embedding 故障不会删除正文。存在 chunk 但向量未完成时，词法检索可降级工作，语义检索排除空向量，并在结果中返回 `degraded_modes=["semantic"]`。

### 7.4 更新、关闭与删除

- 新文件或正文产生新 `content_version`；旧版本保留但不参与检索。
- `in_knowledge_base=false` 后，论文立即被所有检索过滤，不需要等待异步索引删除。
- 普通删除为软删除，写入 `deleted_at`。
- 明确物理删除时，PostgreSQL 外键级联删除正文、chunk、向量、批注和翻译，再由 `purge_file` 任务删除文件。

## 8. 统一检索契约

```text
RetrievalService.search(RetrievalQuery) -> RetrievalResult
```

`RetrievalQuery` 包含 tenant、user、query、scope、mode、top_k，以及来源、年份、作者、标签、全文可用性过滤条件。

`scope` 为 `local`、`external` 或 `hybrid`；`mode` 为 `metadata`、`lexical`、`semantic` 或 `hybrid`。

`RetrievalResult` 分别返回：

- `local_hits`：本地可检索论文及真实证据 chunk。
- `external_candidates`：外部元数据候选，不可直接引用。
- `degraded_modes`：本次未能使用的检索模式。
- query id、embedding profile 和耗时。

每个本地 hit 包含论文、最多 3 个证据 chunk、页码/章节锚点、`can_cite` 和完整 score breakdown。只有当前正文版本的真实 chunk 才能设置 `can_cite=true`。

## 9. 混合检索算法

1. 规范化查询，优先识别 DOI、arXiv ID 和完整标题。
2. 生成中英文 lexical token。
3. 通过 GIN 召回最多 80 个词法 chunk。
4. 使用 Qwen3-Embedding-0.6B 生成 1024 维查询向量。
5. 通过 HNSW cosine 召回最多 80 个语义 chunk。
6. 元数据精确命中作为独立排名列表。
7. 使用 Reciprocal Rank Fusion 合并：`sum(1 / (60 + rank))`。
8. 按论文聚合，保留最多 3 个不同位置的证据 chunk。
9. 返回最终 top_k。

不直接相加 BM25 和 cosine 分数，因为两者尺度不同。默认不施加时间衰减，避免系统性压低经典论文；只有请求显式提供时间过滤或排序时才使用发表时间。

多租户过滤条件会影响近似索引召回，因此每次向量查询事务启用 `SET LOCAL hnsw.iterative_scan = strict_order`，并同时保留 `(tenant_id, user_id)` B-tree 索引。第一阶段使用共享 chunk 表；只有真实规模评测证明需要分区时，才通过后续独立 migration 引入分区，不在租户创建时动态执行 DDL。

## 10. 消费者切换

- MCP `search_papers` 调用 `RetrievalService`。
- `/knowledge/rag/search` 调用同一服务。
- `GET /knowledge` 只做知识库列表，不承担相关性检索。
- 会话 Tool Loop 使用统一结果，不自行决定本地 SQL。
- 写作流程的 local/hybrid 检索使用统一结果，并且只能引用 `can_cite=true` 的证据。
- 外部候选必须经过 acquisition、解析和入库后，才能进入写作证据池。

现有 API 尽量保持路径兼容；响应增加统一检索字段。旧的裸 chunk 或混排外部候选行为不保留。

## 11. 错误处理与恢复

- PostgreSQL 不可用：健康检查失败，相关 API 返回 503，不回退 SQLite 或 JSON。
- pgvector 扩展缺失或 migration 落后：服务拒绝进入 ready 状态。
- 外部搜索失败：保留本地结果并在 `external_error` 中说明，不污染数据库。
- PDF 校验失败：不创建可检索正文，记录失败原因并保留可审计任务。
- 解析失败：论文状态为 `failed`，资产保留，允许用户重试或替换文件。
- embedding 失败：正文和词法检索可用，任务按最多 3 次重试，结果标记语义降级。
- 维度不等于 1024、包含 NaN/Infinity 或零向量：拒绝写入并记录 provider 响应摘要，不记录密钥。
- 并发重复入库：通过 DOI、arXiv ID、SHA256 唯一约束和幂等键返回已有论文。
- Worker 异常退出：超时的 running job 回到 pending，再由其他 worker 领取。

## 12. 可观测性

记录以下指标和 trace metadata：

- ingestion 各阶段耗时、状态和重试次数；
- 解析页数、正文长度、chunk 数和解析质量；
- embedding batch 数、token、延迟和失败率；
- lexical/vector/metadata 候选数；
- RRF 前后排名和 score breakdown；
- 检索降级模式；
- PostgreSQL query latency 和连接池状态；
- HNSW 查询参数与返回数量。

日志必须脱敏数据库密码、API Key、token 和完整论文正文。

## 13. 测试策略

### 13.1 单元测试

- DOI、arXiv ID 和 paper id 规范化。
- 中英文 tokenizer、切片位置和内容 hash。
- RRF 排名、论文聚合和 `can_cite` 判定。
- 状态机和重试规则。
- Qwen embedding 维度、有限值和归一化校验。

### 13.2 PostgreSQL 集成测试

- 使用真实 pgvector Docker 服务运行 migration。
- 两个租户使用相同 `paper_id`、DOI 或文件 SHA256 时互不覆盖。
- RLS 阻止跨租户直接 SQL 读取和写入。
- 事务失败不会留下不完整正文或部分 chunk。
- 更新正文后只检索当前版本。
- 关闭知识库或软删除后立即无法检索。
- 物理删除级联清理 chunk、向量、批注和翻译。
- HNSW 和 GIN 查询在种子数据上被 PostgreSQL planner 使用。

### 13.3 服务与契约测试

- MCP、RAG API、会话和写作流程对同一本地查询得到相同的论文排序和证据。
- 外部候选不会自动写库。
- embedding 故障时词法检索继续工作并声明降级。
- PostgreSQL 不可用时没有本地回退文件产生。
- 下载、解析和 embedding 任务可在进程重启后恢复。

### 13.4 性能基线

提供可重复的检索 benchmark 脚本，种入 50,000 个 chunk，记录 exact、GIN、HNSW 和 hybrid 的 p50/p95、召回数量和查询计划。硬件尚未指定，因此首版不设置脱离硬件的延迟门槛；验收要求查询使用预期索引、不出现全租户无界扫描，并输出可比较的基线报告。

## 14. 部署设计

- Docker 数据库镜像固定为 `pgvector/pgvector:0.8.5-pg17-bookworm`。
- 使用 `SCHOLAR_DATABASE_URL` 作为唯一数据库连接配置。
- backend、worker 和 MCP 使用不同数据库角色；migration 使用 owner 角色。
- Compose 移除 MySQL 服务，新增 PostgreSQL healthcheck 和持久化 volume。
- requirements 移除 `chromadb`，加入 SQLAlchemy、psycopg、Alembic 和 pgvector Python 支持。
- 启动顺序为数据库健康、migration 成功、worker/MCP、backend、frontend。
- 备份同时覆盖 PostgreSQL volume 和论文文件 volume；两者以 paper/asset 元数据关联。

## 15. 实施顺序

1. 建立 PostgreSQL/pgvector 容器、数据库角色、连接层和 Alembic。
2. 将现有关系型运行表迁移为 PostgreSQL schema，删除 SQLite 自动初始化。
3. 实现论文领域表、Repository、RLS 和文件资产规则。
4. 实现持久化 ingestion job、解析、切片和 Qwen embedding 流程。
5. 实现 GIN、HNSW 和 RRF 的 `RetrievalService`。
6. 切换 MCP、知识库 API、会话和写作流程。
7. 删除 Chroma、JSON RAG、MySQL/SQLite 兼容代码与旧配置。
8. 完成集成测试、恢复测试、benchmark 和文档更新。

整个切换不做双写。只有在所有消费者完成 PostgreSQL 切换并通过测试后，才删除旧代码路径。

## 16. 验收标准

- 正式运行只需要 PostgreSQL/pgvector 和文件卷，不生成 SQLite、knowledge JSON、RAG JSON 或 Chroma 数据。
- 论文元数据、正文版本、chunk 与向量存在明确事务和状态边界。
- 同名 paper id 在不同租户和用户之间完全隔离。
- 所有本地检索入口共享统一契约和排序。
- 外部候选不自动入库，也不能直接成为引用证据。
- 删除、关闭知识库和版本更新不会返回陈旧 chunk。
- embedding 服务故障时词法检索可用并明确声明降级。
- PostgreSQL 或 migration 异常时系统失败可见，不静默回退。
- 集成测试在真实 pgvector 容器上通过，并生成可重复性能基线。

## 17. 参考

- pgvector 官方仓库与 HNSW、过滤、iterative scan、hybrid search 文档：https://github.com/pgvector/pgvector
- PostgreSQL GIN 文档：https://www.postgresql.org/docs/current/gin.html
- Qwen3-Embedding 官方仓库：https://github.com/QwenLM/Qwen3-Embedding
- Qwen3-Embedding 官方模型规格：https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
