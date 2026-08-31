from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import re
from typing import Any

from app.papers.models import PaperInput, normalize_arxiv_id, normalize_doi


_ABSTRACT_BOUNDARY_RE = re.compile(
    r"(?:^|\n)\s*(?:abstract|摘要)\s*(?:$|[:：])", re.IGNORECASE | re.MULTILINE
)
_AFFILIATION_RE = re.compile(
    r"\b(?:university|institute|institution|college|school|department|laborator(?:y|ies)|"
    r"research center|research centre|academy|corporation|company|inc\.?|ltd\.?)\b|"
    r"(?:大学|学院|研究院|研究所|实验室|研究中心|公司)",
    re.IGNORECASE,
)
_REVIEW_RE = re.compile(
    r"\b(?:survey|review|systematic review|meta-analysis)\b|(?:综述|调研)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s<>\])},;]+", re.IGNORECASE)


def _field(value: Any, source: str, confidence: float) -> dict[str, Any]:
    return {
        "value": value,
        "source": source,
        "confidence": round(max(0.0, min(float(confidence), 1.0)), 3),
        "user_edited": False,
    }


def _pdf_metadata(parsed_metadata: Mapping[str, Any]) -> dict[str, Any]:
    raw = parsed_metadata.get("pdf_metadata") or {}
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(key).strip().lstrip("/").casefold(): value
        for key, value in raw.items()
        if value not in (None, "")
    }


def _split_authors(value: object) -> list[str]:
    text = " ".join(str(value or "").split())
    if not text:
        return []
    parts = re.split(r"\s*;\s*|\s+and\s+|\s*&\s*", text, flags=re.IGNORECASE)
    if len(parts) == 1 and text.count(",") <= 4:
        parts = re.split(r"\s*,\s*", text)
    return list(dict.fromkeys(part for part in parts if 2 <= len(part) <= 160))


def _institutions(full_text: str, title: str, authors: list[str]) -> list[str]:
    boundary = _ABSTRACT_BOUNDARY_RE.search(full_text)
    preamble = full_text[: boundary.start() if boundary else min(len(full_text), 5000)]
    excluded = {" ".join(value.casefold().split()) for value in (title, *authors) if value}
    result: list[str] = []
    for raw_line in preamble.splitlines():
        line = " ".join(raw_line.split()).strip(" ,;|")
        normalized = line.casefold()
        if (
            not line
            or normalized in excluded
            or len(line) > 240
            or "@" in line
            or _URL_RE.search(line)
            or not _AFFILIATION_RE.search(line)
        ):
            continue
        if line not in result:
            result.append(line)
    return result[:30]


def _published_value(paper: PaperInput, pdf: Mapping[str, Any]) -> tuple[str, str, float]:
    if paper.published_at:
        value = (
            paper.published_at.isoformat()
            if isinstance(paper.published_at, datetime)
            else str(paper.published_at)
        )
        return value, "ingest_input", 0.95
    raw = str(pdf.get("creationdate") or pdf.get("moddate") or "")
    match = re.search(r"(?:D:)?(19|20)\d{2}(?:\d{2})?(?:\d{2})?", raw)
    if not match:
        return "", "not_found", 0.0
    digits = re.sub(r"\D", "", match.group(0).removeprefix("D:"))
    value = digits[:4]
    if len(digits) >= 6:
        value += f"-{digits[4:6]}"
    if len(digits) >= 8:
        value += f"-{digits[6:8]}"
    return value, "pdf_metadata", 0.55


def _venue(paper: PaperInput, pdf: Mapping[str, Any]) -> tuple[str, str, float]:
    for key in ("venue", "publication_venue", "journal", "conference"):
        value = str(paper.metadata.get(key) or "").strip()
        if value:
            return value, f"ingest_metadata.{key}", 0.9
    subject = " ".join(str(pdf.get("subject") or "").split())
    if subject and len(subject) <= 240 and not subject.casefold().startswith("abstract"):
        return subject, "pdf_metadata.subject", 0.45
    return "", "not_found", 0.0


def _links(paper: PaperInput, parsed_metadata: Mapping[str, Any]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {
        "code": [],
        "project": [],
        "dataset": [],
        "supplement": [],
    }

    def add(kind: str, raw: object) -> None:
        candidates = raw if isinstance(raw, (list, tuple, set)) else [raw]
        for candidate in candidates:
            url = str(candidate or "").strip().rstrip(".")
            if _URL_RE.fullmatch(url) and url not in values[kind]:
                values[kind].append(url)

    add("code", parsed_metadata.get("code_urls") or [])
    add("project", parsed_metadata.get("project_urls") or [])
    for key, kind in (
        ("code_url", "code"),
        ("github_url", "code"),
        ("project_url", "project"),
        ("dataset_url", "dataset"),
        ("supplement_url", "supplement"),
    ):
        add(kind, paper.metadata.get(key))
    code = set(values["code"])
    values["project"] = [url for url in values["project"] if url not in code]
    return values


def build_bibliography(
    paper: PaperInput,
    parsed_metadata: Mapping[str, Any],
    full_text: str,
) -> dict[str, dict[str, Any]]:
    """Build deterministic metadata evidence without inventing unavailable values."""

    pdf = _pdf_metadata(parsed_metadata)
    authors = list(paper.authors)
    author_source = "ingest_input"
    author_confidence = 0.95
    if not authors:
        authors = _split_authors(pdf.get("author"))
        author_source = "pdf_metadata.author" if authors else "not_found"
        author_confidence = 0.65 if authors else 0.0

    published_at, published_source, published_confidence = _published_value(paper, pdf)
    venue, venue_source, venue_confidence = _venue(paper, pdf)
    doi = normalize_doi(paper.doi) or normalize_doi(str(parsed_metadata.get("doi") or ""))
    arxiv_id = normalize_arxiv_id(paper.arxiv_id) or normalize_arxiv_id(
        str(parsed_metadata.get("arxiv_id") or "")
    )
    links = _links(paper, parsed_metadata)
    institutions = _institutions(full_text, paper.title, authors)
    paper_type = (
        "review"
        if _REVIEW_RE.search(paper.title)
        else "preprint" if arxiv_id else "research_article"
    )
    type_source = "title_pattern" if paper_type == "review" else "identifier" if arxiv_id else "heuristic"
    type_confidence = 0.9 if paper_type == "review" else 0.95 if arxiv_id else 0.4

    bibliography = {
        "title": _field(paper.title.strip(), "ingest_input", 0.95),
        "title_translation": _field("", "not_generated", 0.0),
        "authors": _field(authors, author_source, author_confidence),
        "institutions": _field(
            institutions,
            "pdf_preamble" if institutions else "not_found",
            0.65 if institutions else 0.0,
        ),
        "published_at": _field(published_at, published_source, published_confidence),
        "venue": _field(venue, venue_source, venue_confidence),
        "doi": _field(
            doi or "",
            "ingest_input" if paper.doi else "pdf_text" if doi else "not_found",
            0.98 if doi else 0.0,
        ),
        "arxiv_id": _field(
            arxiv_id or "",
            "ingest_input" if paper.arxiv_id else "pdf_text" if arxiv_id else "not_found",
            0.98 if arxiv_id else 0.0,
        ),
        "links": _field(
            links,
            "ingest_and_pdf_text" if any(links.values()) else "not_found",
            0.8 if any(links.values()) else 0.0,
        ),
        "paper_type": _field(paper_type, type_source, type_confidence),
    }

    existing = paper.metadata.get("bibliography") or {}
    if isinstance(existing, Mapping):
        for name, value in existing.items():
            if (
                name in bibliography
                and isinstance(value, Mapping)
                and value.get("user_edited") is True
            ):
                bibliography[name] = dict(value)
    return bibliography
