from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any


class SafetyLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    category: str
    safety_level: SafetyLevel
    input_schema: dict[str, Any]
    requires_user_id: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["safety_level"] = self.safety_level.value
        return data


@dataclass
class PaperRecord:
    paper_id: str
    tenant_id: str
    user_id: str
    source: str
    title: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    full_text: str = ""
    published_at: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    url: str | None = None
    file_path: str = ""
    in_knowledge_base: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, tenant_id: str, user_id: str, payload: dict[str, Any]) -> "PaperRecord":
        allowed = {item.name for item in fields(cls)} - {"tenant_id", "user_id"}
        values = {key: value for key, value in payload.items() if key in allowed}
        metadata = dict(values.get("metadata") or {})
        if payload.get("source_records"):
            metadata["source_records"] = payload["source_records"]
        values["metadata"] = metadata
        return cls(tenant_id=tenant_id, user_id=user_id, **values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

