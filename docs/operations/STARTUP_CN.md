# ScholarAgent 中文启动说明

> 本文用于在本地或 Docker 环境启动 ScholarAgent。当前项目的主要访问入口是后端挂载的企业控制台：`http://127.0.0.1:8000/app.html`。

## 1. 启动前确认

请先进入项目根目录：

```powershell
cd <ScholarAgent 项目目录>
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

当前核心依赖包括 FastAPI、Uvicorn、PyMySQL、Redis 客户端、aiohttp、pypdf 等。

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

## 4. MySQL 与 Redis

项目支持 MySQL 优先、JSON fallback。

- MySQL 可用时：任务、会话、知识库、RAG、事件、审计等数据写入 MySQL。
- MySQL 不可用时：部分开发数据会写入 `storage/runtime/*.json`。
- Redis 可用时：用于限流和任务事件流增强。
- Redis 不可用时：会回退到内存实现，但重启后内存状态会丢失。

### 4.1 推荐环境变量

可以复制 `.env.example` 中的配置，或在 PowerShell 中临时设置：

```powershell
$env:SCHOLAR_MYSQL_URL="mysql://scholar:scholar@127.0.0.1:3306/scholar_agent?charset=utf8mb4"
$env:SCHOLAR_REDIS_URL="redis://127.0.0.1:6379/0"
$env:SCHOLAR_STORAGE_BACKEND="auto"
$env:SCHOLAR_ALLOW_MOCK_DATA="false"
$env:SCHOLAR_EXTERNAL_SOURCE_PROVIDER="real"
```

### 4.2 初始化 MySQL

如果本机已经有 MySQL，可以使用管理员连接串初始化数据库、业务用户和表结构：

```powershell
$env:SCHOLAR_MYSQL_ADMIN_URL="mysql://root:你的root密码@127.0.0.1:3306/mysql?charset=utf8mb4"
$env:SCHOLAR_MYSQL_URL="mysql://scholar:scholar@127.0.0.1:3306/scholar_agent?charset=utf8mb4"
.\.venv\Scripts\python.exe scripts\bootstrap_mysql.py
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

### 4.3 通过前端初始化

也可以在前端完成配置：

1. 打开 `http://127.0.0.1:8000/app.html`。
2. 使用 `demo / demo123 / tenant_demo` 登录。
3. 进入“个人中心”。
4. 在模型、数据库、RAG、论文源相关区域填写配置。
5. 使用 MySQL 初始化或模型探测功能验证连接。

运行配置默认保存到：

```text
storage/runtime/runtime_config.json
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

默认 RAG 使用关键词和全文检索，适合本地快速启动：

```powershell
$env:SCHOLAR_RAG_INDEX_BACKEND="auto"
$env:SCHOLAR_RAG_RETRIEVAL_MODE="hybrid"
$env:SCHOLAR_RAG_EMBEDDING_PROVIDER="lexical"
```

如果需要远程 embedding，可以配置 OpenAI-compatible embedding：

```powershell
$env:SCHOLAR_RAG_RETRIEVAL_MODE="hybrid"
$env:SCHOLAR_RAG_EMBEDDING_PROVIDER="openai-compatible"
$env:SCHOLAR_RAG_EMBEDDING_BASE_URL="https://你的embedding服务地址"
$env:SCHOLAR_RAG_EMBEDDING_API_KEY="你的embedding密钥"
$env:SCHOLAR_RAG_EMBEDDING_MODEL="你的embedding模型"
$env:SCHOLAR_RAG_EMBEDDING_DIMENSIONS="1024"
```

常用检查接口：

```text
GET http://127.0.0.1:8000/knowledge/rag/stats
GET http://127.0.0.1:8000/knowledge/rag/search?query=citation&limit=5
```

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

如果你希望用容器启动 MySQL、Redis、后端和前端，可以使用 Docker Compose。

### 8.1 启动 MySQL 和 Redis

```powershell
docker compose --profile prod-deps up -d db redis
```

### 8.2 启动后端和前端

```powershell
docker compose up --build backend frontend
```

访问：

```text
前端：http://127.0.0.1
后端：http://127.0.0.1:8000
```

### 8.3 可选启动 Worker 和 MCP Server

```powershell
docker compose --profile worker up --build worker
docker compose --profile mcp up --build mcp_server
```

当前本地开发模式下，后端 `TaskService` 会直接创建后台任务；独立 Worker 更适合后续生产化部署或任务执行拆分。

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

- `mysql.available`
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

如果启用了 MySQL，检查 `scholar_users` 和 `scholar_tenants` 是否初始化成功。也可以重新执行：

```powershell
.\.venv\Scripts\python.exe scripts\bootstrap_mysql.py
```

### 10.3 写作任务失败

先看 `/health/infra`：

- 模型是否已配置。
- 外部论文源是否可访问。
- 当前租户知识库是否有论文。
- MySQL 是否可用。

如果外部论文源不可用，可以先在“个人知识库”上传论文，再提交写作专项。

### 10.4 Redis 不可用

Redis 不可用时系统会退回内存限流和事件队列。开发阶段可以继续使用，但重启后状态会丢失。需要稳定运行时请启动 Redis。

### 10.5 MySQL 不可用

系统会尝试 JSON fallback，但企业级运行建议使用 MySQL。检查连接串：

```text
SCHOLAR_MYSQL_URL=mysql://用户:密码@主机:端口/数据库?charset=utf8mb4
```

### 10.6 模型探测失败

检查：

- `SCHOLAR_PRIMARY_MODEL_PROVIDER`
- `SCHOLAR_LLM_BASE_URL`
- `SCHOLAR_LLM_API_KEY`
- `SCHOLAR_LLM_MODEL`
- 网络代理或防火墙
- 模型服务是否兼容 OpenAI Chat Completions 接口

## 11. 推荐开发启动流程

日常开发建议使用这个顺序：

1. 启动 MySQL 和 Redis。
2. 激活 `.venv`。
3. 执行 `scripts\bootstrap_mysql.py`。
4. 启动 `uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`。
5. 打开 `http://127.0.0.1:8000/app.html`。
6. 登录 `tenant_demo / demo / demo123`。
7. 在个人中心检查模型、数据库、RAG。
8. 在个人知识库上传或保存论文。
9. 在写作专项提交任务。

## 12. 相关文档

- [架构学习导读](../ARCHITECTURE_LEARNING.md)
- [项目结构规范](../PROJECT_STRUCTURE.md)
- [扩展契约](../EXTENSION_CONTRACT.md)
- [MySQL / Redis / RAG 初始化](MYSQL_REDIS_RAG_SETUP.md)
- [部署资产说明](../../deploy/README.md)
