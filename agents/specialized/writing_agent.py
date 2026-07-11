from __future__ import annotations

from typing import Any, AsyncIterator

from agents.delegation import delegation_service
from app.schemas import UserContext
from agents.skill_registry import skill_registry


class WritingAgent:
    async def run(self, initial_state: dict[str, Any], *, complex_task: bool) -> AsyncIterator[dict[str, Any]]:
        state = dict(initial_state)
        user: UserContext | None = None
        parent_run_id = ""
        if complex_task:
            user = UserContext(tenant_id=state["tenant_id"], user_id=state["user_id"])
            parent_run_id = delegation_service.start_parent(
                user, agent_name="writing_agent", goal=state["topic"], task_id=state.get("task_id", ""),
                payload={"agent_mode": state.get("agent_mode", "auto"), "citation_style": state.get("citation_style")},
            )
            yield {"event":"progress","phase":"delegate_writing_plan","message":"Writing Agent 正在调度研究、结构与引用子 Agent","percent":5,"payload":{"parent_run_id":parent_run_id}}
            children = await delegation_service.run_batch(
                user, parent_run_id=parent_run_id, goal=state["topic"], task_id=state.get("task_id", ""),
                assignments=[
                    {"agent_name":"research_subagent","instruction":"给出检索范围、关键词和证据优先级。","context":state},
                    {"agent_name":"structure_subagent","instruction":"给出章节边界、论证顺序和人机确认点。","context":state},
                    {"agent_name":"citation_subagent","instruction":"给出引用覆盖目标和高风险事实类型。","context":state},
                ],
            )
            state["delegation_results"] = [item.__dict__ for item in children]
            state["agent_parent_run_id"] = parent_run_id
            yield {"event":"progress","phase":"merge_subagent_plan","message":"Writing Agent 已合并子 Agent 规划结果","percent":7,"payload":{"children":state["delegation_results"]}}
        workflow = skill_registry.get_workflow("survey_generation")
        final_result: dict[str, Any] = {}
        try:
            async for event in workflow(state):
                if event.get("event") == "skill_result":
                    final_result = dict(event.get("payload") or {})
                yield event
            if user is not None and parent_run_id:
                delegation_service.finish_parent(
                    user, parent_run_id, status="succeeded",
                    result={
                        "children": state.get("delegation_results", []),
                        "phase": "writing_completed",
                        "citation_audit": final_result.get("citation_audit", {}),
                    },
                )
        except Exception as exc:
            if user is not None and parent_run_id:
                delegation_service.finish_parent(user, parent_run_id, status="failed", error=str(exc))
            raise


writing_agent = WritingAgent()
