from __future__ import annotations

import asyncio
from typing import Any

from agents.skill_registry import skill_registry


async def execute_registered_skill(name: str, state: dict[str, Any], *, timeout_seconds: float = 120) -> dict[str, Any]:
    if not state.get("tenant_id") or not state.get("user_id"):
        raise ValueError("tenant_id and user_id are required")
    for paper in state.get("papers") or []:
        for key in ("tenant_id", "user_id"):
            if paper.get(key) and paper[key] != state[key]:
                raise ValueError("source belongs to another tenant or user")
    result = None
    async with asyncio.timeout(timeout_seconds):
        events = 0
        async for event in skill_registry.get_workflow(name)(dict(state)):
            events += 1
            if events > 256:
                raise ValueError("skill exceeded its direct-execution event budget")
            if event.get("event") == "skill_result":
                result = event.get("payload")
    if not isinstance(result, dict):
        raise ValueError("skill did not produce a structured result")
    if any(result.get(key) != state[key] for key in ("tenant_id", "user_id")):
        raise ValueError("skill result scope mismatch")
    return result
