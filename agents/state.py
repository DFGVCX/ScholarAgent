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
    citation_style: str
    max_papers: int
    active_skill: str
    skill_result: dict[str, Any]
    final_report: str
    reflection_logs: list[dict[str, Any]]
    error: str | None

