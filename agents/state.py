from __future__ import annotations

from typing import Any, TypedDict


class GlobalState(TypedDict, total=False):
    task_id: str
    tenant_id: str
    user_id: str
    trace_id: str
    topic: str
    input_type: str
    input_value: str
    retrieval_strategy: str
    retrieval_constraints: str
    citation_style: str
    max_papers: int
    active_skill: str
    skill_name: str
    agent_mode: str
    require_outline_confirmation: bool
    route_decision: dict[str, Any]
    skill_result: dict[str, Any]
    global_review: dict[str, Any]
    quality_gate: dict[str, Any]
    quality_retry_count: int
    task_graph: dict[str, Any]
    final_result: dict[str, Any]
    final_report: str
    reflection_logs: list[dict[str, Any]]
    error: str | None

