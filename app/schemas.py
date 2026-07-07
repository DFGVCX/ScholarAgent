from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class InputType(str, Enum):
    PDF = "pdf"
    ARXIV = "arxiv"
    DOI = "doi"


class CitationStyle(str, Enum):
    IEEE = "IEEE"
    APA = "APA"
    GB_T_7714 = "GB/T 7714"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class UserContext:
    tenant_id: str
    user_id: str
    api_key_label: str = "api-key"


@dataclass(frozen=True)
class SurveyTaskRequest:
    topic: str
    input_type: InputType
    input_value: str = ""
    citation_style: CitationStyle = CitationStyle.IEEE
    max_papers: int = 12
    require_outline_confirmation: bool = False

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "SurveyTaskRequest":
        return cls(
            topic=str(payload.get("topic", "")).strip(),
            input_type=InputType(str(payload.get("input_type", "arxiv")).lower()),
            input_value=str(payload.get("input_value", "")).strip(),
            citation_style=CitationStyle(payload.get("citation_style", "IEEE")),
            max_papers=int(payload.get("max_papers", 12)),
            require_outline_confirmation=bool(payload.get("require_outline_confirmation", False)),
        )


@dataclass
class TaskRecord:
    task_id: str
    tenant_id: str
    user_id: str
    status: TaskStatus
    phase: str
    request: dict[str, Any]
    percent: int = 0
    trace_id: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class TaskEvent:
    event: str
    task_id: str
    phase: str
    message: str
    percent: int
    payload: dict[str, Any] = field(default_factory=dict)
    tenant_id: str = ""
    user_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
