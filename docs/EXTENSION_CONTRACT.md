# Extension Contract

Use this contract when adding backend features, MCP tools, or new atomic skills. The goal is to keep each capability independently testable and easy to wire into the UI.

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
        "payload": {"result": "..."},
    }
```

Then register it in `agents/skill_registry.py`:

```python
"<skill_name>": SkillDescriptor(
    name="<skill_name>",
    module_path="skills.<skill_name>.main_workflow",
    workflow_attr="run_<skill_name>_workflow",
)
```

## Skill Event Contract

| Field | Required | Meaning |
|---|---|---|
| `event` | Yes | `progress`, `outline_required`, `skill_result`, `failed`, or a documented custom event |
| `phase` | Yes | Stable machine-readable phase name |
| `message` | Yes | Short user-facing Chinese message |
| `percent` | Yes | Integer progress from 0 to 100 |
| `payload` | Yes | Structured data used by frontend/task persistence |

The final skill result must include `tenant_id`, `user_id`, and enough structured payload for audit, rendering, and persistence.

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
