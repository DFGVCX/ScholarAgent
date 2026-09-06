# Extension Contract

Use this contract when adding backend features, MCP tools, or new atomic skills. The goal is to keep each capability independently testable and easy to wire into the UI.

Agent 调用预算、缓存和 Skill 候选沉淀遵循 [product/AGENT_EFFICIENCY_AND_EVOLUTION.md](product/AGENT_EFFICIENCY_AND_EVOLUTION.md)。候选 Skill 不得绕过人工审核直接写入生产 `skills/`。

## Add A Backend Feature

1. Define request/response DTOs in `app/schemas.py` or a feature-specific schema module when the schema grows large.
2. Add the API route in `app/routes/<feature>.py`.
3. Put business logic in `app/services/<feature>_service.py`.
4. Put persistence logic in `app/services/<feature>_store.py` or extend `mysql_store.py` only for shared table access.
5. Register the router in `app/main.py`.
6. Add tests in `tests/test_<feature>.py` or `tests/api/`.
7. Update `docs/PROJECT_STRUCTURE.md` when a new directory or boundary appears.

Routes must handle authentication, tenant context, validation, and HTTP errors. Services should not know about FastAPI request objects.

## Add An Atomic Skill

Create a folder under `skills/<skill_name>/`:

```text
skills/<skill_name>/
├── __init__.py
├── SKILL.md
├── main_workflow.py
├── state.py
└── tools/
    ├── __init__.py
    └── <tool>.py
```

Start by copying `skills/_template/` when possible.

The workflow entry must be an async generator:

```python
async def run_<skill_name>_workflow(initial_state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    yield {
        "event": "progress",
        "phase": "prepare",
        "message": "Preparing skill",
        "percent": 10,
        "payload": {},
    }
    yield {
        "event": "skill_result",
        "phase": "<skill_name>",
        "message": "Skill result ready",
        "percent": 94,
        "payload": {
            "tenant_id": initial_state["tenant_id"],
            "user_id": initial_state["user_id"],
            "result": "...",
        },
    }
```

Declare the entrypoint in the folder's `SKILL.md` frontmatter. No main-router edit is required:

```yaml
---
name: example_skill
module: skills.example_skill.main_workflow
entrypoint: run_example_skill_workflow
version: 1.0.0
description: A reviewed, independently executable capability.
enabled: true
---
```

The registry discovers additions, manifest updates, disablement and removal on the next lookup.
Python implementation changes in already imported modules require a worker restart; this is not arbitrary Python hot-reloading.
Only reviewed code may enter `skills/`. Generated candidates remain outside the production registry.

Use `agents.skill_execution.execute_registered_skill` for bounded, tenant-scoped direct execution.
The standard MCP tools `list_skills` and `execute_skill` expose discovery and invocation. Long-running
`survey_generation` is submitted to TaskService rather than holding an MCP request open for the full writing job.
Built-in independent packages are `paper_retrieval`, `survey_generation`, `citation_audit`, and `citation_formatting`.

## Skill Event Contract

| Field | Required | Meaning |
|---|---|---|
| `event` | Yes | `progress`, `outline_required`, `skill_result`, `failed`, or a documented custom event |
| `phase` | Yes | Stable machine-readable phase name |
| `message` | Yes | Short user-facing Chinese message |
| `percent` | Yes | Integer progress from 0 to 100 |
| `payload` | Yes | Structured data used by frontend/task persistence |

The final skill result must include `tenant_id`, `user_id`, and enough structured payload for audit, rendering, and persistence.

## Add A Writing Lifecycle Capability

Lifecycle capabilities are executable nodes, not prompt-only advisory agents.

1. Implement an executor with `async execute(state, node) -> (output, quality)` under `agents/specialized/` or the owning Skill package.
2. Register it in `CAPABILITY_EXECUTORS` and expose the capability to `TaskGraphPlan` validation.
3. Give every node a stable `node_id`, semantic `capability`, and explicit `version`.
4. Declare dependencies in `depends_on`; never read an undeclared upstream output.
5. Persist attempts through `NodeRunStore`. Do not create a second private cache inside the executor.
6. Return structured quality data. A recoverable failure must include one `retry_target` rather than raising at the quality boundary.
7. Invalidation includes the target and graph descendants only. Independent completed nodes must remain reusable.
8. Add tests proving execution counts across a retry, not merely the presence of graph node names.

Node cache identity is computed from capability input plus a stable dependency snapshot. Runtime labels such as `completed` versus `reused` must not change the fingerprint.

Section caches depend on the section definition, actual evidence, instruction and memory snapshot, not the whole outline run ID.
Quality review must distinguish source existence, evidence location and semantic support. A model verdict without an exact source quote cannot pass.
Run `python scripts/run_capability_acceptance.py` for offline contract and real-StateGraph control-flow tests; this does not replace external-service acceptance.

## Add An MCP Tool

1. Add models or adapters under `mcp_server/scholar_mcp/`.
2. Register the tool with the existing registry in `mcp_server/scholar_mcp/tools.py`.
3. Assign a safety level in the tool spec.
4. Keep tenant/user inputs explicit.
5. Add tests in `tests/test_mcp_registry.py` or a dedicated test file.

## Add Frontend UI For A Capability

1. Add API wrapper code under `frontend/src/api/` for typed frontend work, or centralize helpers inside `frontend/dist/app.html` while it remains the active zero-build console.
2. Add page/domain logic under `frontend/src/pages/<feature>/` for future Vite migration.
3. Keep route names aligned with backend API names.
4. Do not hard-code fake results when the backend has a real route; show empty/error/loading states instead.
