# ScholarAgent

## PDF chunking comparison

ScholarAgent keeps five versioned parsing/chunking strategies. `scholar_hierarchical_v4` uses Docling first, falls back to `multimodal_aware_v3`, and creates hierarchy-aware prose, equation, table, figure, and algorithm chunks. The four historical strategies remain available for regression comparisons.

```powershell
python scripts/compare_chunk_strategies.py `
  --corpus-jsonl evaluation/corpus.jsonl `
  --queries-jsonl evaluation/queries.jsonl `
  --output evaluation/reports/chunk-comparison.json
```

Corpus JSONL uses one local PDF per line:

```json
{"paper_id":"paper-1","title":"Federated Learning Paper","path":"E:/papers/paper.pdf"}
```

Query JSONL may label a relevant paper, section, and/or page range:

```json
{"query":"联邦学习是什么","relevant":{"paper_id":"paper-1","section_ids":["introduction"],"page_ranges":[[1,2]]}}
```

The report contains Recall@K, Precision@K, MRR, nDCG@K, complete returned chunk text, and page/section provenance. A query without `relevant` is emitted as a diagnostic ranking only; ScholarAgent does not call unlabeled similarity scores “accuracy” or “recall”.

ScholarAgent 是一个面向多租户科研写作场景的智能体项目，包含 FastAPI 后端、企业级 Web 控制台、MCP 风格论文检索边界、RAG 知识库和可独立扩展的写作原子能力。

## 项目结构

```text
ScholarAgent/
├── app/                 # 后端 API、业务服务、仓储、任务 Worker
├── agents/              # Agent 编排、技能路由、质量评估
├── skills/              # 可插拔原子能力，每个能力独立目录
├── mcp_server/          # 论文源、检索、知识库工具边界
├── frontend/            # 前端源码与可部署静态页面
├── deploy/              # Docker、Nginx 与 PostgreSQL 部署资产
├── scripts/             # 初始化与运维脚本
└── tests/               # 单元测试、接口测试、工作流测试、E2E 说明
```

本仓库只保留项目运行和协作开发需要的代码、配置模板与测试资产。个人学习文档、本地运行数据、模型权重、上传文件和历史草稿默认不进入 Git。

## 快速启动

推荐直接使用 Docker Compose：

```powershell
docker compose --profile setup run --rm --build docling_models
docker compose up --build -d
docker compose ps
```

第一条命令会把 v4 所需的 layout、TableFormer 和 code/formula 模型预取到 `scholar_storage` 卷；成功执行一次后不用重复下载。打开 `http://127.0.0.1:3000/`，在运行配置中显式选择 `scholar_hierarchical_v4`。模型暂不可用时会自动回退到 v3，不会丢失论文；解析结果会记录 requested/actual parser，便于确认是否真的走了 Docling。数据库升级会由 `migrate` 服务自动执行到 Alembic `20260828_0005`。

以下是不用 Compose、逐个启动服务的开发方式：

1. 创建虚拟环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

2. 复制环境变量模板并按本机服务修改：

```powershell
Copy-Item .env.example .env
```

3. 启动 PostgreSQL/pgvector、执行迁移并初始化基础数据：

```powershell
docker compose up -d db
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe scripts\init_infra.py
```

4. 启动 Browser Worker（机构登录与知网下载）：

```powershell
.\scripts\start_browser_worker.ps1
```

5. 启动 MCP Server：

```powershell
.\.venv\Scripts\python.exe mcp_server\server.py --transport streamable-http --host 127.0.0.1 --port 8001
```

6. 启动后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

7. 访问前端：

```text
http://127.0.0.1:8000/app.html
```

## 常用入口

- 后端 API：`app/main.py`
- 任务服务：`app/services/task_service.py`
- RAG 服务：`app/services/rag_service.py`
- 会话服务：`app/services/conversation_service.py`
- 写作技能：`skills/survey_generation/`
- 前端页面：`frontend/dist/app.html`
- 前端源码：`frontend/src/`
- 部署配置：`deploy/`、`docker-compose.yml`

## Windows 安装版

`test-release` 分支提供去本地化的 Windows 安装包构建。安装版将数据库、知识库、批注和运行配置保存到 `%LOCALAPPDATA%\ScholarAgent`，不要求最终用户安装 Python、Node.js、MySQL、Redis 或 Docker。

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows_release.ps1 -Version 0.2.0
```

构建产物为 `release/output/ScholarAgent-Setup-0.2.0.exe`。详细说明见 `docs/operations/WINDOWS_RELEASE.md`。
