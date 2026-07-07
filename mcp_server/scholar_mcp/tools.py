from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from app.config import get_settings
from mcp_server.scholar_mcp.external_sources import (
    ExternalSourceError,
    attach_arxiv_pdf,
    attach_paper_pdf,
    fetch_arxiv_paper,
    fetch_crossref_paper,
    search_arxiv_papers,
    search_crossref_papers,
    search_openalex_papers,
)
from mcp_server.scholar_mcp.models import PaperRecord, SafetyLevel
from mcp_server.scholar_mcp.registry import scholar_tool, tool_registry
from mcp_server.scholar_mcp.safety import evaluate_tool_safety
from mcp_server.scholar_mcp.store import knowledge_store


def _normalize_id(value: str) -> str:
    return value.strip().replace("/", "_").replace(":", "_").replace(" ", "_")


def _paper_id(source: str, value: str) -> str:
    return f"paper:{source}:{_normalize_id(value)}"


def _synthetic_paper(
    tenant_id: str,
    user_id: str,
    source: str,
    value: str,
    topic: str = "",
    index: int = 0,
) -> PaperRecord:
    stable = hashlib.sha1(f"{source}:{value}:{index}".encode("utf-8")).hexdigest()[:8]
    source_value = value if index == 0 else f"{value}-{index}"
    title_topic = topic or value or "ScholarAgent"
    return PaperRecord(
        paper_id=_paper_id(source, source_value),
        tenant_id=tenant_id,
        user_id=user_id,
        source=source,
        title=f"{title_topic} Study {index + 1}",
        authors=["ScholarAgent Research Group"],
        abstract=(
            f"This paper discusses {title_topic} with emphasis on reproducible "
            f"pipelines, evaluation, and source-grounded academic writing. Ref {stable}."
        ),
        published_at=f"202{index % 6}-01-01",
        doi=value if source == "doi" else None,
        arxiv_id=value if source == "arxiv" else None,
        metadata={"synthetic": True, "stable_hash": stable},
    )


def _mock_external_sources_enabled() -> bool:
    settings = get_settings()
    if settings.external_source_provider != "mock":
        return False
    if not settings.allow_mock_data:
        raise RuntimeError(
            "SCHOLAR_EXTERNAL_SOURCE_PROVIDER=mock requires SCHOLAR_ALLOW_MOCK_DATA=true"
        )
    return True


def _extract_local_document(
    tenant_id: str,
    user_id: str,
    input_value: str,
    topic: str,
    task_id: str,
) -> PaperRecord:
    path = Path(input_value)
    if not path.exists():
        raise RuntimeError(f"Local document does not exist: {input_value}")
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        full_text = path.read_text(encoding="utf-8", errors="ignore")
    elif suffix == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError as exc:
            raise RuntimeError("PDF ingestion requires the pypdf package to parse real files") from exc
        try:
            reader = PdfReader(str(path))
            full_text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except Exception as exc:
            raise RuntimeError(f"Could not parse PDF {path.name}: {exc}") from exc
        if not full_text:
            raise RuntimeError(f"PDF {path.name} did not contain extractable text")
    else:
        raise RuntimeError(f"Unsupported local document type: {suffix or 'unknown'}")
    value = path.stem or task_id or "uploaded_document"
    return PaperRecord(
        paper_id=_paper_id("pdf", value),
        tenant_id=tenant_id,
        user_id=user_id,
        source="pdf",
        title=topic or path.stem,
        authors=[],
        abstract=full_text[:900],
        full_text=full_text,
        metadata={
            "external_source": "local_file",
            "mock": False,
            "file_name": path.name,
            "content_length": len(full_text),
        },
    )


@scholar_tool(
    name="TOOL_LIST",
    description="List discoverable Scholar MCP tools without full schemas",
    category="meta",
    safety_level=SafetyLevel.LOW,
    requires_user_id=False,
)
async def tool_list() -> dict[str, Any]:
    return {"tools": tool_registry.list_specs()}


@scholar_tool(
    name="TOOL_GET",
    description="Get full schema and safety metadata for a Scholar MCP tool",
    category="meta",
    safety_level=SafetyLevel.LOW,
    requires_user_id=False,
)
async def tool_get(tool_name: str) -> dict[str, Any]:
    return {"tool": tool_registry.get_spec(tool_name).to_dict()}


@scholar_tool(
    name="ingest_paper",
    description="Normalize PDF, arXiv, or DOI input into a tenant-scoped paper record",
    category="ingestion",
    safety_level=SafetyLevel.MEDIUM,
)
async def ingest_paper(
    tenant_id: str,
    user_id: str,
    input_type: str,
    input_value: str,
    topic: str = "",
    task_id: str = "",
) -> dict[str, Any]:
    source = input_type.lower()
    if _mock_external_sources_enabled():
        if source not in {"pdf", "doi", "arxiv"}:
            raise ValueError(f"unsupported input_type: {input_type}")
        value = Path(input_value).stem if source == "pdf" else input_value
        paper = _synthetic_paper(tenant_id, user_id, source, value, topic or value)
        paper.metadata["mock_provider"] = "SCHOLAR_EXTERNAL_SOURCE_PROVIDER=mock"
    else:
        if source == "pdf":
            paper = await asyncio.to_thread(_extract_local_document, tenant_id, user_id, input_value, topic, task_id)
        elif source == "doi":
            paper = await asyncio.to_thread(fetch_crossref_paper, tenant_id, user_id, input_value, topic)
        elif source == "arxiv":
            paper = await asyncio.to_thread(fetch_arxiv_paper, tenant_id, user_id, input_value, topic)
        else:
            raise ValueError(f"unsupported input_type: {input_type}")
    await knowledge_store.save_paper(paper)
    return {"paper": paper.to_dict()}


async def _save_external_search_results(papers: list[PaperRecord]) -> list[dict[str, Any]]:
    saved: list[dict[str, Any]] = []
    for paper in papers:
        if paper.source == "arxiv":
            paper = await asyncio.to_thread(attach_arxiv_pdf, paper)
        elif paper.metadata.get("pdf_url") and not paper.metadata.get("pdf_download_error"):
            paper = await asyncio.to_thread(attach_paper_pdf, paper, [str(paper.metadata["pdf_url"])])
        saved.append(await knowledge_store.save_paper(paper))
    return saved


def _external_sources_for(source: str) -> list[tuple[str, Any]]:
    if source == "arxiv":
        return [("arxiv", search_arxiv_papers)]
    if source == "openalex":
        return [("openalex", search_openalex_papers)]
    if source == "crossref":
        return [("crossref", search_crossref_papers)]
    return [
        ("openalex", search_openalex_papers),
        ("arxiv", search_arxiv_papers),
        ("crossref", search_crossref_papers),
    ]


@scholar_tool(
    name="search_papers",
    description="Search tenant knowledge and, when configured, real external arXiv metadata",
    category="search",
    safety_level=SafetyLevel.LOW,
)
async def search_papers(
    tenant_id: str,
    user_id: str,
    query: str,
    source: str = "all",
    limit: int = 12,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    external_error: str | None = None
    if source in {"all", "local"}:
        items.extend(await knowledge_store.search(tenant_id, user_id, query, limit))
    if source in {"all", "arxiv", "openalex", "crossref"} and len(items) < limit:
        if _mock_external_sources_enabled():
            needed = limit - len(items)
            items.extend(
                _synthetic_paper(tenant_id, user_id, "arxiv", query or "survey", query, i).to_dict()
                for i in range(needed)
            )
        else:
            external_errors: list[str] = []
            for source_name, search_fn in _external_sources_for(source):
                needed = limit - len({item["paper_id"]: item for item in items})
                if needed <= 0:
                    break
                try:
                    papers = await asyncio.to_thread(search_fn, tenant_id, user_id, query, needed)
                    items.extend(await _save_external_search_results(papers))
                except ExternalSourceError as exc:
                    external_errors.append(f"{source_name}: {exc}")
                    if source == source_name:
                        raise RuntimeError(str(exc)) from exc
            external_error = " | ".join(external_errors) if external_errors else None
    unique: dict[str, dict[str, Any]] = {item["paper_id"]: item for item in items}
    return {
        "items": list(unique.values())[:limit],
        "has_more": False,
        "next_cursor": None,
        "external_error": external_error,
    }


@scholar_tool(
    name="save_to_knowledge",
    description="Save a normalized paper to the user's personal knowledge base",
    category="knowledge",
    safety_level=SafetyLevel.MEDIUM,
)
async def save_to_knowledge(tenant_id: str, user_id: str, paper: dict[str, Any]) -> dict[str, Any]:
    record = PaperRecord(tenant_id=tenant_id, user_id=user_id, **paper)
    return {"paper": await knowledge_store.save_paper(record)}


@scholar_tool(
    name="verify_citations",
    description="Verify generated citation IDs against the active paper pool",
    category="citation",
    safety_level=SafetyLevel.LOW,
)
async def verify_citations(
    tenant_id: str,
    user_id: str,
    text: str,
    paper_pool: list[dict[str, Any]],
) -> dict[str, Any]:
    from skills.survey_generation.tools.citation import CitationGuard

    return {"audit": CitationGuard().verify_citations(text, paper_pool)}


@scholar_tool(
    name="delete_knowledge",
    description="Delete a paper from tenant knowledge base after explicit confirmation",
    category="knowledge",
    safety_level=SafetyLevel.HIGH,
)
async def delete_knowledge(
    tenant_id: str,
    user_id: str,
    paper_id: str,
    confirmation_token: str = "",
) -> dict[str, Any]:
    deleted = await knowledge_store.delete(tenant_id, user_id, paper_id)
    return {"deleted": deleted, "paper_id": paper_id}


async def call_tool_with_safety(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    spec = tool_registry.get_spec(name)
    decision = evaluate_tool_safety(spec, arguments)
    if not decision.allowed:
        return {
            "status": "REQUIRE_CONFIRM" if decision.require_confirmation else "DENIED",
            "safety": decision.to_dict(),
            "tool": name,
        }
    result = await tool_registry.call(name, arguments)
    return {"status": "OK", **result}
