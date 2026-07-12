from __future__ import annotations

from typing import Any, TypedDict


class SurveyState(TypedDict, total=False):
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
    papers: list[dict[str, Any]]
    chunks: list[list[dict[str, Any]]]
    outline: list[dict[str, Any]]
    sections: list[dict[str, Any]]
    citation_audit: dict[str, Any]
    references: list[str]
    reflection_logs: list[dict[str, Any]]
    markdown: str

