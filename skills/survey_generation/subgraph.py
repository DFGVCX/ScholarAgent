from __future__ import annotations

from typing import Any

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from agents.skill_registry import skill_registry
from agents.specialized.writing_lifecycle import LifecycleNodeRunner
from agents.state import GlobalState
from agents.task_graph import TaskGraphPlan, dynamic_task_planner
from app.services.node_run_store import node_run_store
from skills.survey_generation.tools.formatter import CitationFormatter
from skills.survey_generation.tools.refiner import LCERefiner


async def _plan_task(state: GlobalState) -> dict[str, Any]:
    skills = [
        {
            "name": item.name,
            "version": item.version,
            "description": item.description,
            "enabled": item.enabled,
        }
        for item in skill_registry.list_skills()
    ]
    plan = await dynamic_task_planner.plan_writing_with_model(
        str(state.get("topic") or ""), dict(state), skills
    )
    if state.get("retry_target"):
        node_run_store.invalidate(plan, dict(state), str(state["retry_target"]))
    get_stream_writer()({
        "event": "progress",
        "phase": "plan_task_graph",
        "message": "Planner generated the writing task graph",
        "percent": 8,
        "payload": plan.to_dict(),
    })
    return {"task_graph": plan.to_dict(), "task_graph_plan": plan.to_dict()}


def build_lifecycle_graph(plan: TaskGraphPlan):
    builder = StateGraph(GlobalState)
    quality_node = plan.node_for_capability("quality_review")

    for node in plan.nodes:
        async def execute(state: GlobalState, selected=node) -> dict[str, Any]:
            return await LifecycleNodeRunner(plan, get_stream_writer()).run(selected, dict(state))

        builder.add_node(node.node_id, execute)

    async def finish(state: GlobalState) -> dict[str, Any]:
        decision = dict(state.get("quality_decision") or {})
        if not decision.get("passed"):
            raise RuntimeError(f"Writing quality did not converge: {decision.get('findings')}")
        papers = list(state.get("papers") or [])
        audit = dict(state.get("citation_audit") or {})
        cited_ids = set(audit.get("found_ids") or [])
        cited_papers = [paper for paper in papers if paper.get("paper_id") in cited_ids]
        formatter = CitationFormatter()
        references = formatter.batch_process(cited_papers, str(state.get("citation_style") or "IEEE"))
        markdown = LCERefiner().merge_sections(
            str(state.get("topic") or ""), list(state.get("sections") or []), references
        )
        result = {
            "task_id": state.get("task_id"),
            "tenant_id": state.get("tenant_id"),
            "user_id": state.get("user_id"),
            "topic": state.get("topic"),
            "markdown": markdown,
            "outline": list(state.get("outline") or []),
            "outline_markdown": state.get("outline_markdown", ""),
            "papers": papers,
            "sections": list(state.get("sections") or []),
            "references": references,
            "formatter_status": formatter.status(),
            "citation_audit": audit,
            "quality_decision": decision,
            "task_graph": plan.to_dict(),
            "node_runs": node_run_store.list_task_runs(dict(state)),
            "retry_history": list(state.get("retry_history") or []),
        }
        get_stream_writer()({
            "event": "skill_result", "phase": "survey_generation",
            "message": "Writing lifecycle completed", "percent": 94, "payload": result,
        })
        return {"skill_result": result, "node_runs": result["node_runs"]}

    async def failed(state: GlobalState) -> dict[str, Any]:
        decision = dict(state.get("quality_decision") or {})
        raise RuntimeError(
            f"Writing quality failed after targeted retries; target={decision.get('retry_target')}: "
            f"{decision.get('findings')}"
        )

    builder.add_node("finish_lifecycle", finish)
    builder.add_node("fail_lifecycle", failed)

    roots = [node for node in plan.nodes if not node.depends_on]
    for root in roots:
        builder.add_edge(START, root.node_id)
    for node in plan.nodes:
        if node.node_id == quality_node.node_id:
            continue
        for candidate in plan.nodes:
            if node.node_id in candidate.depends_on:
                builder.add_edge(node.node_id, candidate.node_id)

    def quality_route(state: GlobalState) -> str:
        decision = dict(state.get("quality_decision") or {})
        if decision.get("passed"):
            return "finish_lifecycle"
        if int(state.get("quality_retry_count") or 0) > 2:
            return "fail_lifecycle"
        retry_target = str(decision.get("retry_target") or "section_writing")
        invalidated = node_run_store.invalidate(plan, dict(state), retry_target)
        get_stream_writer()({
            "event": "progress",
            "phase": "targeted_retry",
            "message": f"Quality review routed execution back to {retry_target}",
            "percent": 84,
            "payload": {
                "status": "retrying",
                "retry_target": retry_target,
                "invalidated_nodes": invalidated,
                "retry_count": int(state.get("quality_retry_count") or 0),
                "findings": decision.get("findings") or [],
            },
        })
        if retry_target.startswith("section:"):
            return plan.node_for_capability("section_writing").node_id
        capability = {
            "retrieval": "literature_retrieval",
            "outline": "outline_generation",
            "section_writing": "section_writing",
            "quality": "quality_review",
        }.get(retry_target, retry_target)
        try:
            return plan.node_for_capability(capability).node_id
        except StopIteration:
            return plan.node_for_capability("section_writing").node_id

    route_targets = {node.node_id: node.node_id for node in plan.nodes}
    route_targets |= {"finish_lifecycle": "finish_lifecycle", "fail_lifecycle": "fail_lifecycle"}
    builder.add_conditional_edges(quality_node.node_id, quality_route, route_targets)
    builder.add_edge("finish_lifecycle", END)
    builder.add_edge("fail_lifecycle", END)
    return builder.compile()


async def _execute_lifecycle(state: GlobalState) -> dict[str, Any]:
    plan = TaskGraphPlan.from_dict(dict(state["task_graph_plan"]))
    lifecycle = build_lifecycle_graph(plan)
    final_state: dict[str, Any] = {}
    writer = get_stream_writer()
    async for mode, chunk in lifecycle.astream(dict(state), stream_mode=["custom", "values"]):
        if mode == "custom":
            writer(chunk)
        elif mode == "values":
            final_state = dict(chunk)
    result = dict(final_state.get("skill_result") or {})
    if not result:
        raise RuntimeError("Dynamic writing lifecycle did not produce a result")
    return {
        "skill_result": result,
        "node_runs": list(final_state.get("node_runs") or []),
        "quality_retry_count": int(final_state.get("quality_retry_count") or 0),
        "retry_history": list(final_state.get("retry_history") or []),
    }


def build_survey_subgraph():
    builder = StateGraph(GlobalState)
    builder.add_node("plan_task_graph", _plan_task)
    builder.add_node("execute_lifecycle", _execute_lifecycle)
    builder.add_edge(START, "plan_task_graph")
    builder.add_edge("plan_task_graph", "execute_lifecycle")
    builder.add_edge("execute_lifecycle", END)
    return builder.compile()


survey_subgraph = build_survey_subgraph()
