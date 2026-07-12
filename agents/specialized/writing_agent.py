from __future__ import annotations

from typing import Any, AsyncIterator

from agents.delegation import delegation_service
from agents.skill_registry import skill_registry
from agents.task_graph import TaskNode, dynamic_task_planner, task_graph_executor
from app.schemas import UserContext


class WritingAgent:
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
                    "citation_style": state.get("citation_style"),
                },
            )
            plan = dynamic_task_planner.plan_writing(str(state["topic"]), state)
            yield {
                "event": "progress",
                "phase": "delegate_writing_plan",
                "message": "写作 Agent 已生成依赖感知的任务计划",
                "percent": 5,
                "payload": {"parent_run_id": parent_run_id, "task_graph": plan.to_dict()},
            }

            async def run_task(node: TaskNode, dependencies: dict[str, Any]):
                results = await delegation_service.run_batch(
                    user,
                    parent_run_id=parent_run_id,
                    goal=state["topic"],
                    task_id=state.get("task_id", ""),
                    assignments=[{
                        "agent_name": node.agent_name,
                        "instruction": node.instruction,
                        "context": {**state, "dependency_results": dependencies},
                    }],
                )
                return results[0]

            children = await task_graph_executor.execute(plan, run_task)
            state["delegation_results"] = [item.__dict__ for item in children]
            state["agent_parent_run_id"] = parent_run_id
            state["task_graph"] = plan.to_dict()
            yield {
                "event": "progress",
                "phase": "merge_subagent_plan",
                "message": "写作 Agent 已合并任务图执行结果",
                "percent": 7,
                "payload": {
                    "children": state["delegation_results"],
                    "task_graph": state["task_graph"],
                },
            }

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
                        "children": state.get("delegation_results", []),
                        "task_graph": state.get("task_graph", {}),
                        "phase": "writing_completed",
                        "citation_audit": final_result.get("citation_audit", {}),
                    },
                )
        except Exception as exc:
            if user is not None and parent_run_id:
                delegation_service.finish_parent(
                    user, parent_run_id, status="failed", error=str(exc)
                )
            raise


writing_agent = WritingAgent()
