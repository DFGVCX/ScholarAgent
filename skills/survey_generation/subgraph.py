from __future__ import annotations

from typing import Any, Literal

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from agents.state import GlobalState
from skills.survey_generation.main_workflow import run_survey_pipeline


async def _run_pipeline(state: GlobalState) -> dict[str, Any]:
    result: dict[str, Any] | None = None
    writer = get_stream_writer()
    async for event in run_survey_pipeline(dict(state)):
        writer(event)
        if event.get("event") == "skill_result":
            result = dict(event.get("payload") or {})
    if result is None:
        raise RuntimeError("survey_generation did not produce a skill result")
    return {"skill_result": result}


async def _quality_gate(state: GlobalState) -> dict[str, Any]:
    result = dict(state.get("skill_result") or {})
    audit = dict(result.get("citation_audit") or {})
    retry_count = int(state.get("quality_retry_count") or 0)
    passed = bool(result.get("markdown")) and bool(audit.get("is_valid", False))
    return {
        "quality_gate": {
            "passed": passed,
            "retry_count": retry_count,
            "reason": "ok" if passed else "missing_markdown_or_invalid_citations",
        }
    }


def _quality_route(state: GlobalState) -> Literal["targeted_retry", "finish"]:
    gate = dict(state.get("quality_gate") or {})
    if not gate.get("passed") and int(gate.get("retry_count") or 0) < 1:
        return "targeted_retry"
    return "finish"


async def _targeted_retry(state: GlobalState) -> dict[str, Any]:
    writer = get_stream_writer()
    writer({
        "event": "progress",
        "phase": "targeted_retry",
        "message": "质量门未通过，正在局部重试写作 Skill",
        "percent": 90,
        "payload": {"retry_count": int(state.get("quality_retry_count") or 0) + 1},
    })
    return {"quality_retry_count": int(state.get("quality_retry_count") or 0) + 1}


async def _finish(state: GlobalState) -> dict[str, Any]:
    gate = dict(state.get("quality_gate") or {})
    if not gate.get("passed"):
        raise RuntimeError(f"Writing Skill quality gate failed: {gate.get('reason')}")
    return {"skill_result": dict(state.get("skill_result") or {})}


def build_survey_subgraph():
    builder = StateGraph(GlobalState)
    builder.add_node("run_pipeline", _run_pipeline)
    builder.add_node("quality_gate", _quality_gate)
    builder.add_node("targeted_retry", _targeted_retry)
    builder.add_node("finish", _finish)
    builder.add_edge(START, "run_pipeline")
    builder.add_edge("run_pipeline", "quality_gate")
    builder.add_conditional_edges("quality_gate", _quality_route)
    builder.add_edge("targeted_retry", "run_pipeline")
    builder.add_edge("finish", END)
    return builder.compile()


survey_subgraph = build_survey_subgraph()
