# ScholarAgent

ScholarAgent 是一个面向多租户科研研究、文献管理和学术写作场景的智能体系统。项目提供统一调度 Agent、可插拔 Skill、标准 MCP 工具服务、混合 RAG、机构文献访问、论文阅读与翻译，以及可观测的后台写作工作流。

## 核心能力

- 会话 Agent：保存目标、来源、阶段、待确认动作和调度理由，支持跨轮继续执行。
- 写作 Agent：编排大纲、检索、章节生成、质量检查和引用审计；复杂任务才启用子 Agent。
- Tool Loop：完成工具发现、参数规划、风险确认、结果观察和继续推理。
- 个人知识库：租户隔离的论文、全文、批注、译文与混合检索。
- 机构访问：独立 Browser Worker 保持学校 VPN、WebVPN 或数据库登录会话。
- 模型工厂：支持 OpenAI-compatible、Claude、Qwen、DeepSeek、Ollama、vLLM 等提供方。

## 项目结构

```text
ScholarAgent/
├── app/                 # 后端 API、业务服务、仓储、任务 Worker
├── agents/              # Agent 编排、技能路由、质量评估
├── skills/              # 可插拔原子能力，每个能力独立目录
├── mcp_server/          # 论文源、检索、知识库工具边界
├── browser_worker/      # 机构认证浏览器会话、知网检索与受控下载
├── frontend/            # 前端源码与可部署静态页面
├── deploy/              # Docker、Nginx、MySQL 初始化脚本
├── docs/                # 项目架构、扩展契约和启动说明
├── scripts/             # 初始化与运维脚本
└── tests/               # 单元测试、接口测试、工作流测试、E2E 说明
```

本仓库只保留项目运行和协作开发需要的代码、配置模板与测试资产。个人学习文档、本地运行数据、模型权重、上传文件和历史草稿默认不进入 Git。

## 架构边界

```text
Web / API
   ↓
app（认证、租户、会话、任务和服务编排）
   ↓
agents（上下文、调度、Tool Loop、Agent 协作）
   ↓
skills（独立业务能力） ──→ mcp_server（标准工具与数据源边界）
                                  ↓
                         browser_worker（认证浏览器）
```

- API 路由只负责认证、校验和 HTTP 协议转换。
- 业务规则位于 `app/services/`，Agent 决策位于 `agents/`。
- Skill 不直接依赖前端或 FastAPI；外部论文能力通过 MCP 调用。
- Browser Worker 独立维护浏览器上下文，后端不保存机构账号密码。
- 所有会话、论文、向量、批注、译文和任务均携带租户与用户边界。

详细规范见 `docs/PROJECT_STRUCTURE.md` 和 `docs/EXTENSION_CONTRACT.md`。

## 本地启动

1. 创建虚拟环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

2. 复制环境变量模板并按本机服务修改：

```powershell
Copy-Item .env.example .env
```

Linux 或 macOS 可执行：

```bash
cp .env.example .env
```

3. 初始化数据库和基础数据：

```powershell
.\.venv\Scripts\python.exe scripts\bootstrap_mysql.py
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

健康检查：`GET /health`；基础设施检查：`GET /health/infra`。

## 容器部署

1. 从发布模板创建环境文件并替换所有 `replace-*` 值：

```bash
cp .env.release.example .env
```

2. 启动数据库、Redis、MCP、后端和前端：

```bash
docker compose --profile prod-deps --profile mcp up -d --build
```

生产模式会拒绝以下配置：演示 API Key、Mock 数据和通配符 CORS。模型密钥、数据库密码、MCP Token 与 Browser Worker Token 只能通过环境变量或密钥管理服务注入，不得提交到 Git。

Browser Worker 涉及可见登录或本机浏览器时，建议作为受控节点独立运行，并通过 `SCHOLAR_BROWSER_WORKER_URL` 和 `SCHOLAR_BROWSER_WORKER_TOKEN` 接入。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

测试覆盖认证与租户隔离、上下文记忆、可解释调度、工具确认、知识库、混合检索、机构访问、论文获取、翻译和写作工作流。

## 常用入口

- 后端 API：`app/main.py`
- 任务服务：`app/services/task_service.py`
- RAG 服务：`app/services/rag_service.py`
- 会话服务：`app/services/conversation_service.py`
- 写作技能：`skills/survey_generation/`
- 前端页面：`frontend/dist/app.html`
- 前端源码：`frontend/src/`
- 部署配置：`deploy/`、`docker-compose.yml`
- 发布配置模板：`.env.release.example`
