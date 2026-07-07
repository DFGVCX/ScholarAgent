from __future__ import annotations

from typing import Any, AsyncIterator


async def run_template_workflow(initial_state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """Replace this with the new skill workflow."""

    yield {
        "event": "progress",
        "phase": "prepare",
        "message": "Preparing skill",
        "percent": 10,
        "payload": {},
    }

    result = {
        "task_id": initial_state.get("task_id"),
        "tenant_id": initial_state.get("tenant_id"),
        "user_id": initial_state.get("user_id"),
        "result": {},
    }
    yield {
        "event": "skill_result",
        "phase": "template",
        "message": "Skill result ready",
        "percent": 94,
        "payload": result,
    }

