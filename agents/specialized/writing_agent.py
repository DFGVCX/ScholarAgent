from __future__ import annotations

from typing import Any, AsyncIterator

from agents.delegation import delegation_service
from agents.skill_registry import skill_registry
from app.schemas import UserContext


class WritingAgent:
    """Parent agent for the planner-authored writing lifecycle."""

    async def run(
        self, initial_state: dict[str, Any], *, complex_task: bool
    ) -> AsyncIterator[dict[str, Any]]:
        state = dict(initial_state)
        user: UserContext | None = None
        parent_run_id = ""
        if complex_task:
            user = UserContext(tenant_id=state["tenant_id"], user_id=state["user_id"])
            parent_run_id = delegation_service.start_parent(
                user,
                agent_name="writing_agent",
                goal=state["topic"],
                task_id=state.get("task_id", ""),
                payload={
                    "agent_mode": state.get("agent_mode", "auto"),
                    "execution": "dynamic_lifecycle_skill_agents",
                },
            )
            state["agent_parent_run_id"] = parent_run_id

        workflow = skill_registry.get_workflow("survey_generation")
        final_result: dict[str, Any] = {}
        try:
            async for event in workflow(state):
                if event.get("event") == "skill_result":
                    final_result = dict(event.get("payload") or {})
                yield event
            if user is not None and parent_run_id:
                delegation_service.finish_parent(
                    user,
                    parent_run_id,
                    status="succeeded",
                    result={
                        "task_graph": final_result.get("task_graph", {}),
                        "node_runs": final_result.get("node_runs", []),
                        "quality_decision": final_result.get("quality_decision", {}),
                    },
                )
        except Exception as exc:
            if user is not None and parent_run_id:
                delegation_service.finish_parent(user, parent_run_id, status="failed", error=str(exc))
            raise


writing_agent = WritingAgent()
