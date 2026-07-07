from __future__ import annotations

from typing import Any, TypedDict


class TemplateSkillState(TypedDict, total=False):
    task_id: str
    tenant_id: str
    user_id: str
    payload: dict[str, Any]

