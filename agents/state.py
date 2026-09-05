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
    global_retry_count: int
    retry_target: str
    retry_history: list[dict[str, Any]]
    task_graph: dict[str, Any]
    task_graph_plan: dict[str, Any]
    node_snapshots: dict[str, Any]
    node_runs: list[dict[str, Any]]
    memory_context: dict[str, Any]
    historical_preferences: dict[str, Any]
    papers: list[dict[str, Any]]
    chunks: list[list[dict[str, Any]]]
    outline: list[dict[str, Any]]
    outline_markdown: str
    outline_node_id: str
    sections: list[dict[str, Any]]
    section_reviews: list[dict[str, Any]]
    quality_decision: dict[str, Any]
    citation_audit: dict[str, Any]
    final_result: dict[str, Any]
    final_report: str
    reflection_logs: list[dict[str, Any]]
    error: str | None

