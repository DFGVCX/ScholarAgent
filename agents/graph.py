from __future__ import annotations

from typing import Any, AsyncIterator

from agents.evaluator import GlobalEvaluator
from agents.skill_registry import skill_registry
from agents.orchestrator import general_orchestrator
from agents.specialized import writing_agent


async def run_global_workflow(initial_state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """LangGraph-compatible global orchestration.

    This keeps the state-machine boundary explicit while remaining runnable in a
    dependency-light local environment.
    """

    active_skill = str(initial_state.get("skill_name") or "survey_generation")
    decision = general_orchestrator.decide(
        str(initial_state.get("topic") or ""),
        "survey_review" if active_skill == "survey_generation" else "general_assistant",
    )
    requested_mode = str(initial_state.get("agent_mode") or "auto")
    use_delegation = (
        requested_mode == "multi_agent"
        or (requested_mode == "auto" and decision.execution_mode == "delegation")
    )
    execution_mode = "delegation" if use_delegation else "skill"
    yield {
        "event": "progress",
        "phase": "route_task",
        "message": f"Routing task to {active_skill} skill",
        "percent": 3,
        "payload": {"active_skill": active_skill, "target_agent": decision.target_agent,
                    "execution_mode": execution_mode, "requested_mode": requested_mode,
                    "complexity": decision.complexity,
                    "reasons": list(decision.reasons)},
    }

    workflow = None if active_skill == "survey_generation" else skill_registry.get_workflow(active_skill)
    skill_result: dict[str, Any] | None = None
    event_stream = writing_agent.run(initial_state, complex_task=use_delegation) if active_skill == "survey_generation" else workflow(initial_state)
    async for event in event_stream:
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
