from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping
from uuid import UUID
import re


def normalize_doi(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", normalized)
    normalized = re.sub(r"^doi:\s*", "", normalized)
    return normalized or None


def normalize_arxiv_id(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    normalized = re.sub(r"^https?://arxiv\.org/(?:abs|pdf)/", "", normalized)
    normalized = re.sub(r"^arxiv:\s*", "", normalized)
    normalized = re.sub(r"\.pdf$", "", normalized)
    normalized = re.sub(r"v\d+$", "", normalized)
    return normalized or None


@dataclass(frozen=True)
class PaperInput:
    paper_id: str
    source: str
    title: str
    authors: tuple[str, ...] = ()
    abstract: str = ""
    full_text: str = ""
    published_at: str | datetime | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    url: str | None = None
    file_uri: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    file_sha256: str | None = None
    file_size: int | None = None
    in_knowledge_base: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.paper_id.strip():
            raise ValueError("paper_id is required")
        if not self.source.strip():
            raise ValueError("source is required")
        if not self.title.strip():
            raise ValueError("title is required")
        if self.file_size is not None and self.file_size < 0:
            raise ValueError("file_size cannot be negative")


@dataclass(frozen=True)
class PaperRecord:
    paper_uuid: UUID
    tenant_id: str
    user_id: str
    paper_id: str
    source: str
    title: str
    authors: tuple[str, ...]
    abstract: str
    published_at: datetime | None
    doi: str | None
    arxiv_id: str | None
    url: str | None
    in_knowledge_base: bool
    ingestion_status: str
    current_content_version: int
    metadata: Mapping[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ContentVersion:
    paper_uuid: UUID
    content_uuid: UUID
    content_version: int
    chunk_count: int
    parse_status: str = "ready"
    parser_name: str = "legacy_fixed"
    parser_version: str = "1"
    chunk_strategy: str = "legacy_fixed"
    chunker_version: str = "1"
