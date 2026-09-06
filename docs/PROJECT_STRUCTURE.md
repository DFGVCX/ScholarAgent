# ScholarAgent Project Structure

This file is the source of truth for where new files belong.

## Runtime Code

| Directory | Owner | Purpose | Put Here | Do Not Put Here |
|---|---|---|---|---|
| `app/` | Backend API | FastAPI routes, DTOs, services, repositories, background workers | API routes, schemas, service logic, persistence adapters | Frontend UI, raw research docs, model weights |
| `agents/` | Orchestration | Global workflow routing, skill registry, model factory, evaluation | Cross-skill routing and orchestration | Skill-specific business logic |
| `skills/` | Atomic capability layer | Independent skills such as survey generation | One folder per skill, skill workflow, skill tools | API route handlers, tenant auth, storage bootstrap |
| `mcp_server/` | Tool boundary | Paper search, ingestion, knowledge tools, safety registry | MCP tools, external source adapters, paper models | Web page code, UI state |
| `browser_worker/` | Authenticated browser runtime | Institution login sessions, CNKI navigation, official downloads | Browser session lifecycle and source-specific browser adapters | General web scraping, Agent planning, API business logic |
| `frontend/` | Web console | Frontend source and deployable static bundle | `src`, `dist`, package config, frontend docs | Backend services, Python scripts |
| `deploy/` | Deployment | Dockerfiles, nginx config, database init SQL | Container and infra config | Runtime uploads or logs |
| `scripts/` | Operations | One-off setup and bootstrap commands | PostgreSQL/pgvector validation, seed data, maintenance helpers | Long-running application services |
| `tests/` | Quality | Unit/API/workflow/E2E tests | Test suites and fixtures | Production code |

## Project Knowledge

| Directory | Purpose |
|---|---|
| `docs/product/` | Product architecture and implementation rationale |
| `docs/operations/` | Startup, infrastructure, database, RAG, deployment guidance |
| `docs/quality/` | Acceptance criteria and release evidence |
| `docs/history/` | Historical conversations and old decisions |
| `docs/assets/` | PDFs, screenshots, and non-code assets |
| `aiem_specs/` | ADP/AIEM-style engineering standards copied into this project |
| `.agents/` | Codex skills and local automation guidance |
| `archive/` | Legacy demos and retired files that must not be treated as active code |

## Runtime Data

| Directory | Purpose | Git Policy |
|---|---|---|
| `storage/` | Local JSON fallback, uploads, annotations, generated tenant artifacts | Keep directory docs only; do not commit tenant data |
| `logs/` | Local process logs | Do not commit generated logs |
| `models/` | Optional local base models and adapters | Do not commit large weights unless explicitly approved |

## Naming Rules

- Backend route files use plural nouns: `tasks.py`, `knowledge.py`, `conversations.py`.
- Backend services use `_service.py` for business services and `_store.py` for persistence adapters.
- Atomic skills use snake case directory names: `skills/survey_generation/`, `skills/citation_audit/`.
- Frontend source keeps domain folders under `frontend/src/pages/<feature>/`.
- Documentation uses English file names for stable links, while headings may be Chinese.

## Boundary Rules

- `app/routes/**` may call `app/services/**`; routes should not call `skills/**` directly.
- `app/services/task_service.py` is the bridge from API task creation to `agents.graph`.
- `agents/` may route to `skills/`, but `skills/` should not route to other skills without going through `agents/`.
- `skills/**` may call `mcp_server.scholar_mcp.client` for paper/knowledge tools.
- `mcp_server/**` owns external source access and knowledge-tool safety checks.
- `browser_worker/**` owns authenticated browser state; it is called through `app/services/browser_worker_client.py` and never imports API routes.
- `frontend/**` calls backend APIs through `frontend/src/api` or the static app's centralized request helpers.

## Agent Runtime Boundaries

| Package | Responsibility |
|---|---|
| `agents/context/` | Conversation compression, event recall and prompt assembly |
| `agents/specialized/` | Domain Agent coordinators such as the writing Agent |
| `agents/specialized/writing_lifecycle.py` | Executable retrieval, outline, section-writing, and quality Skill Agents |
| `agents/orchestrator.py` | Explainable route decision and complex-task delegation |
| `agents/conversation_tool_loop.py` | Tool discovery, planning, confirmation, observation and continuation |
| `agents/delegation.py` | Parent/child Agent run lifecycle and result aggregation |

Simple requests should stay in a Tool or Skill path. Subagents are created only when the route decision marks a task as complex or the caller explicitly requests multi-Agent execution.

## Dynamic Writing Lifecycle

The writing path is no longer a monolithic predefined pipeline.

1. `agents/task_graph.py` asks the configured model for a structured `TaskGraphPlan`. The goal, memory, retry state, and discovered `SKILL.md` descriptors are included in planning input. A bounded four-node plan is used only when model planning is unavailable or invalid.
2. `skills/survey_generation/subgraph.py` compiles that plan into a real LangGraph at runtime. Retrieval, outline generation, section writing, and quality review are separate graph nodes.
3. `app/services/node_run_store.py` persists every node attempt in `scholar_task_node_runs`, including input, output, node version, quality result, input fingerprint, and dependency snapshot.
4. Section writing additionally persists one record per `section:<section_id>`. A failed section invalidates only that section, the section aggregator, quality review, and their downstream state.
5. The Quality Skill Agent must return one explicit `retry_target`: `retrieval`, `outline`, `section_writing`, `quality`, or `section:<section_id>`.
6. LangGraph conditional edges route back to that target. Completed upstream nodes are reused when their input fingerprint, version, and dependency snapshot still match.
7. Global review follows the same contract and may re-enter the writing Skill with a targeted retry instead of terminating immediately.
