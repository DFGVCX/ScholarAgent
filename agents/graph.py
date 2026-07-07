from __future__ import annotations

from typing import Any, AsyncIterator

from agents.evaluator import GlobalEvaluator
from agents.skill_registry import skill_registry


async def run_global_workflow(initial_state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """LangGraph-compatible global orchestration.

    This keeps the state-machine boundary explicit while remaining runnable in a
    dependency-light local environment.
    """

    active_skill = str(initial_state.get("skill_name") or "survey_generation")
    yield {
        "event": "progress",
        "phase": "route_task",
        "message": f"Routing task to {active_skill} skill",
        "percent": 3,
        "payload": {"active_skill": active_skill},
    }

    workflow = skill_registry.get_workflow(active_skill)
    skill_result: dict[str, Any] | None = None
    async for event in workflow(initial_state):
        yield event
        if event.get("event") == "skill_result":
            skill_result = event.get("payload", {})

    if skill_result is None:
        raise RuntimeError(f"{active_skill} did not return a result")

    review = GlobalEvaluator().evaluate(skill_result)
    yield {
        "event": "progress",
        "phase": "global_review",
        "message": "Global review completed",
        "percent": 96,
        "payload": review,
    }
    if not review["passed"]:
        raise RuntimeError(f"Global review failed: {review['findings']}")

    yield {
        "event": "completed",
        "phase": "completed",
        "message": "Survey generation finished",
        "percent": 100,
        "payload": skill_result | {"global_review": review},
    }


class WorkflowApp:
    async def astream(self, initial_state: dict[str, Any]) -> AsyncIterator[dict[str, dict[str, Any]]]:
        async for event in run_global_workflow(initial_state):
            yield {event["phase"]: event}


app = WorkflowApp()
