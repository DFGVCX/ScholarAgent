from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncIterator

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from agents.evaluator import GlobalEvaluator
from agents.checkpointing import checkpoint_provider
from agents.orchestrator import general_orchestrator
from agents.skill_registry import skill_registry
from agents.specialized import writing_agent
from agents.state import GlobalState
from app.services.tracing import trace_recorder


async def _route_task(state: GlobalState) -> dict[str, Any]:
    active_skill = str(state.get("skill_name") or "survey_generation")
    decision = general_orchestrator.decide(
        str(state.get("topic") or ""),
        "survey_review" if active_skill == "survey_generation" else "general_assistant",
    )
    requested_mode = str(state.get("agent_mode") or "auto")
    use_delegation = (
        requested_mode == "multi_agent"
        or (requested_mode == "auto" and decision.execution_mode == "delegation")
    )
    route = decision.to_dict() | {
        "active_skill": active_skill,
        "requested_mode": requested_mode,
        "execution_mode": "delegation" if use_delegation else "skill",
        "use_delegation": use_delegation,
    }
    get_stream_writer()({
        "event": "progress",
        "phase": "route_task",
        "message": f"Routing task to {active_skill} skill",
        "percent": 3,
        "payload": route,
    })
    return {"active_skill": active_skill, "route_decision": route}


async def _execute_skill(state: GlobalState) -> dict[str, Any]:
    active_skill = str(state["active_skill"])
    route = dict(state.get("route_decision") or {})
    if active_skill == "survey_generation":
        stream = writing_agent.run(dict(state), complex_task=bool(route.get("use_delegation")))
    else:
        stream = skill_registry.get_workflow(active_skill)(dict(state))

    skill_result: dict[str, Any] | None = None
    writer = get_stream_writer()
    async for event in stream:
        writer(event)
        if event.get("event") == "skill_result":
            skill_result = dict(event.get("payload") or {})
    if skill_result is None:
        raise RuntimeError(f"{active_skill} did not return a result")
    return {"skill_result": skill_result}


async def _global_review(state: GlobalState) -> dict[str, Any]:
    review = GlobalEvaluator().evaluate(dict(state.get("skill_result") or {}))
    get_stream_writer()({
        "event": "progress",
        "phase": "global_review",
        "message": "Global review completed",
        "percent": 96,
        "payload": review,
    })
    if not review["passed"]:
        raise RuntimeError(f"Global review failed: {review['findings']}")
    return {"global_review": review}


async def _finalize(state: GlobalState) -> dict[str, Any]:
    result = dict(state.get("skill_result") or {}) | {
        "global_review": dict(state.get("global_review") or {})
    }
    get_stream_writer()({
        "event": "completed",
        "phase": "completed",
        "message": "Survey generation finished",
        "percent": 100,
        "payload": result,
    })
    return {"final_result": result}


def build_global_graph(checkpointer: Any | None = None):
    builder = StateGraph(GlobalState)
    builder.add_node("route_task", _route_task)
    builder.add_node("execute_skill", _execute_skill)
    builder.add_node("global_review", _global_review)
    builder.add_node("finalize", _finalize)
    builder.add_edge(START, "route_task")
    builder.add_edge("route_task", "execute_skill")
    builder.add_edge("execute_skill", "global_review")
    builder.add_edge("global_review", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer or InMemorySaver())


app = build_global_graph()
_runtime_app: Any | None = None
_runtime_lock = asyncio.Lock()


async def _get_runtime_app():
    global _runtime_app
    if os.getenv("SCHOLAR_CHECKPOINT_BACKEND", "postgres").strip().lower() == "memory":
        return app
    async with _runtime_lock:
        if _runtime_app is None:
            _runtime_app = build_global_graph(await checkpoint_provider.get())
        return _runtime_app


async def run_global_workflow(initial_state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """Run the compiled LangGraph while preserving the public SSE event contract."""
    thread_id = str(
        initial_state.get("task_id")
        or initial_state.get("trace_id")
        or f"workflow-{id(initial_state)}"
    )
    config = {"configurable": {"thread_id": thread_id}}
    runtime_app = await _get_runtime_app()
    async for event in runtime_app.astream(
        dict(initial_state), config=config, stream_mode="custom"
    ):
        trace_recorder.record(
            str(initial_state.get("trace_id") or thread_id),
            str(event.get("phase") or "workflow"),
            "workflow",
            task_id=str(initial_state.get("task_id") or "") or None,
            tenant_id=str(initial_state.get("tenant_id") or "") or None,
            user_id=str(initial_state.get("user_id") or "") or None,
            metadata={
                "event": event.get("event"),
                "message": event.get("message"),
                "percent": event.get("percent"),
                "payload": event.get("payload"),
            },
        )
        yield event


class WorkflowApp:
    async def astream(self, initial_state: dict[str, Any]) -> AsyncIterator[dict[str, dict[str, Any]]]:
        async for event in run_global_workflow(initial_state):
            yield {event["phase"]: event}


workflow_app = WorkflowApp()
