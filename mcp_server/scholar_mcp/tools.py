from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.schemas import UserContext
from app.services.institutional_access.service import institutional_access_service
from app.services.browser_worker_client import browser_worker_client
from app.services.rag_service import rag_service
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
    is_mock = _mock_external_sources_enabled()
    if is_mock:
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
    # Synthetic fixtures are scoped to the current run. Persisting them would
    # contaminate tenant retrieval and make repeated test runs progressively slower.
    if not is_mock:
        await knowledge_store.save_paper(paper)
    return {"paper": paper.to_dict()}


async def _prepare_external_search_results(
    papers: list[PaperRecord],
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for paper in papers:
        # Topic search is metadata-only. Full text is acquired only after the user
        # selects a candidate, avoiding bandwidth waste and unintended bulk downloads.
        paper.metadata["full_text_available"] = bool(
            paper.source == "arxiv"
            or paper.arxiv_id
            or (paper.metadata.get("is_oa") and paper.metadata.get("pdf_url"))
        )
        candidate = paper.to_dict()
        candidate["can_cite"] = False
        candidate["acquisition_required"] = True
        prepared.append(candidate)
    return prepared


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
    persist_results: bool = False,
) -> dict[str, Any]:
    local_hits: list[dict[str, Any]] = []
    external_candidates: list[dict[str, Any]] = []
    external_error: str | None = None
    if source in {"all", "local"}:
        if query.strip():
            retrieval = await rag_service.search(tenant_id, user_id, query, limit)
            for hit in retrieval.get("local_hits") or retrieval.get("items") or []:
                document = await knowledge_store.get(tenant_id, user_id, str(hit["paper_id"]))
                local_hits.append({**(document or {}), **hit, "can_cite": True})
        else:
            local_hits = await knowledge_store.search(tenant_id, user_id, "", limit)
            for item in local_hits:
                item["can_cite"] = item.get("ingestion_status") in {"ready", "failed"}
    if source in {"all", "external", "arxiv", "openalex", "crossref"} and len(local_hits) < limit:
        if _mock_external_sources_enabled():
            needed = limit - len(local_hits)
            for i in range(needed):
                candidate = _synthetic_paper(
                    tenant_id, user_id, "arxiv", query or "survey", query, i
                ).to_dict()
                candidate.update({"can_cite": False, "acquisition_required": True})
                external_candidates.append(candidate)
        else:
            external_errors: list[str] = []
            for source_name, search_fn in _external_sources_for(source):
                needed = limit - len(local_hits) - len(
                    {item["paper_id"]: item for item in external_candidates}
                )
                if needed <= 0:
                    break
                try:
                    papers = await asyncio.to_thread(search_fn, tenant_id, user_id, query, needed)
                    external_candidates.extend(await _prepare_external_search_results(papers))
                except ExternalSourceError as exc:
                    external_errors.append(f"{source_name}: {exc}")
                    if source == source_name:
                        raise RuntimeError(str(exc)) from exc
            external_error = " | ".join(external_errors) if external_errors else None
    unique_local: dict[str, dict[str, Any]] = {item["paper_id"]: item for item in local_hits}
    unique_external: dict[str, dict[str, Any]] = {
        item["paper_id"]: item for item in external_candidates
    }
    local_values = list(unique_local.values())[:limit]
    external_values = list(unique_external.values())[: max(0, limit - len(local_values))]
    return {
        "items": [*local_values, *external_values],
        "local_hits": local_values,
        "external_candidates": external_values,
        "retrieval_mode": "hybrid_rrf" if query.strip() and local_values else "metadata",
        "has_more": False,
        "next_cursor": None,
        "external_error": external_error,
        "persist_results": False,
        "persistence_ignored": bool(persist_results),
    }


@scholar_tool(
    name="save_to_knowledge",
    description="Save a normalized paper to the user's personal knowledge base",
    category="knowledge",
    safety_level=SafetyLevel.MEDIUM,
)
async def save_to_knowledge(tenant_id: str, user_id: str, paper: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(paper)
    normalized.pop("tenant_id", None)
    normalized.pop("user_id", None)
    record = PaperRecord(tenant_id=tenant_id, user_id=user_id, **normalized)
    return {"paper": await knowledge_store.save_paper(record)}


@scholar_tool(
    name="acquire_paper_to_knowledge",
    description="Download the selected real paper full text and save it to the tenant knowledge base",
    category="knowledge",
    safety_level=SafetyLevel.MEDIUM,
)
async def acquire_paper_to_knowledge(
    tenant_id: str,
    user_id: str,
    paper: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(paper)
    normalized.pop("tenant_id", None)
    normalized.pop("user_id", None)
    record = PaperRecord(tenant_id=tenant_id, user_id=user_id, **normalized)
    if record.file_path and Path(record.file_path).exists():
        acquired = record
    elif record.source == "arxiv" or record.arxiv_id:
        acquired = await asyncio.to_thread(attach_arxiv_pdf, record)
    else:
        candidates = [
            str(value)
            for value in (
                record.metadata.get("pdf_url"),
                record.metadata.get("landing_page_url"),
                record.url,
            )
            if value
        ]
        acquired = await asyncio.to_thread(attach_paper_pdf, record, candidates)
    if not acquired.file_path or not Path(acquired.file_path).exists():
        reason = acquired.metadata.get("pdf_download_error") or "候选来源没有提供可下载全文"
        raise RuntimeError(f"未能获取该论文全文：{reason}")
    acquired.metadata["created_from"] = "conversation_selected_acquisition"
    acquired.metadata["full_text_available"] = True
    return {"paper": await knowledge_store.save_paper(acquired), "acquired": True}


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


@scholar_tool(
    name="toggle_knowledge_base",
    description="Toggle a paper's membership in the vector knowledge base",
    category="knowledge",
    safety_level=SafetyLevel.MEDIUM,
)
async def toggle_knowledge_base(
    tenant_id: str,
    user_id: str,
    paper_id: str,
    in_knowledge_base: bool = True,
) -> dict[str, Any]:
    result = await knowledge_store.toggle_kb(tenant_id, user_id, paper_id, in_knowledge_base)
    return {"paper_id": paper_id, "in_knowledge_base": result}


@scholar_tool(
    name="institution_session_status",
    description="Read the current user's institution access session status",
    category="institutional_access",
    safety_level=SafetyLevel.LOW,
)
async def institution_session_status(
    tenant_id: str,
    user_id: str,
    session_id: str = "",
) -> dict[str, Any]:
    user = UserContext(tenant_id=tenant_id, user_id=user_id)
    return {"session": institutional_access_service.status(user, session_id)}


@scholar_tool(
    name="start_institution_login",
    description="Start a user-visible institution login session from a saved profile",
    category="institutional_access",
    safety_level=SafetyLevel.MEDIUM,
)
async def start_institution_login(
    tenant_id: str,
    user_id: str,
    profile_id: str = "",
) -> dict[str, Any]:
    user = UserContext(tenant_id=tenant_id, user_id=user_id)
    if not profile_id:
        profiles = institutional_access_service.list_profiles(user)
        if not profiles:
            raise RuntimeError("请先在个人中心添加机构访问配置")
        profile_id = str(profiles[0]["profile_id"])
    session = institutional_access_service.start_session(user, profile_id)
    browser = await browser_worker_client.start_session(
        session_id=session["session_id"],
        tenant_id=tenant_id,
        user_id=user_id,
        login_url=session["login_url"],
        headless=False,
    )
    return {"session": session, "browser": browser, "browser_managed": True}


@scholar_tool(
    name="confirm_institution_browser_login",
    description="Confirm that the user completed login in the visible institution browser",
    category="institutional_access",
    safety_level=SafetyLevel.MEDIUM,
)
async def confirm_institution_browser_login(
    tenant_id: str,
    user_id: str,
    session_id: str = "",
) -> dict[str, Any]:
    user = UserContext(tenant_id=tenant_id, user_id=user_id)
    if not session_id:
        session_id = str(institutional_access_service.status(user).get("session_id") or "")
    if not session_id:
        raise RuntimeError("当前没有机构浏览器会话")
    browser = await browser_worker_client.mark_authenticated(session_id)
    session = institutional_access_service.activate_browser_session(
        user, session_id, str(browser.get("current_url") or "")
    )
    return {"session": session, "browser": browser}


async def _recover_system_vpn_browser(
    user: UserContext, session: dict[str, Any]
) -> bool:
    profile = next(
        (
            item for item in institutional_access_service.list_profiles(user)
            if str(item.get("profile_id") or "") == str(session.get("profile_id") or "")
        ),
        None,
    )
    if not profile or profile.get("access_type") != "system_vpn":
        return False
    session_id = str(session.get("session_id") or "")
    if not session_id:
        return False
    try:
        await browser_worker_client.close(session_id)
    except Exception:
        pass
    await browser_worker_client.start_session(
        session_id=session_id,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        login_url=str(profile.get("login_url") or "https://www.cnki.net/"),
        headless=False,
    )
    browser = await browser_worker_client.mark_authenticated(session_id)
    institutional_access_service.activate_browser_session(
        user, session_id, str(browser.get("current_url") or profile.get("login_url") or "")
    )
    return True


@scholar_tool(
    name="search_cnki_papers",
    description="Search CNKI in the current authenticated institution browser session",
    category="institutional_access",
    safety_level=SafetyLevel.LOW,
)
async def search_cnki_papers(
    tenant_id: str,
    user_id: str,
    query: str,
    limit: int = 20,
    session_id: str = "",
) -> dict[str, Any]:
    user = UserContext(tenant_id=tenant_id, user_id=user_id)
    if not session_id:
        session_id = str(institutional_access_service.status(user).get("session_id") or "")
    if not session_id:
        raise RuntimeError("当前没有机构浏览器会话，请先连接机构并完成登录")
    session = institutional_access_service.status(user, session_id)
    if session.get("status") == "active":
        try:
            browser_status = await browser_worker_client.status(session_id)
            if browser_status.get("status") == "closed":
                raise RuntimeError("browser context is closed")
            result = await browser_worker_client.search_cnki(
                session_id, query, min(max(1, limit), 50)
            )
            return {"items": result.get("items", []), "session_id": session_id, "browser": result}
        except Exception as exc:
            if not await _recover_system_vpn_browser(user, session):
                institutional_access_service.mark_browser_unavailable(user, session_id, str(exc))
                raise RuntimeError("机构浏览器已经关闭，请重新连接机构并完成登录") from exc
            result = await browser_worker_client.search_cnki(
                session_id, query, min(max(1, limit), 50)
            )
            return {"items": result.get("items", []), "session_id": session_id, "browser": result}
    if await _recover_system_vpn_browser(user, session):
        result = await browser_worker_client.search_cnki(
            session_id, query, min(max(1, limit), 50)
        )
        return {"items": result.get("items", []), "session_id": session_id, "browser": result}
    if session.get("status") != "active":
        raise RuntimeError("机构登录已失效，请重新连接并在可见浏览器完成学校登录")
    try:
        await browser_worker_client.status(session_id)
    except Exception as exc:
        institutional_access_service.mark_browser_unavailable(user, session_id, str(exc))
        raise RuntimeError("机构浏览器已经关闭，请重新连接并完成登录") from exc
    result = await browser_worker_client.search_cnki(session_id, query, min(max(1, limit), 50))
    return {"items": result.get("items", []), "session_id": session_id, "browser": result}


@scholar_tool(
    name="download_cnki_selections",
    description="Download selected CNKI results in the authenticated browser and ingest them",
    category="institutional_access",
    safety_level=SafetyLevel.HIGH,
)
async def download_cnki_selections(
    tenant_id: str,
    user_id: str,
    indexes: list[int],
    session_id: str = "",
    confirmation_token: str = "",
) -> dict[str, Any]:
    user = UserContext(tenant_id=tenant_id, user_id=user_id)
    if not session_id:
        session_id = str(institutional_access_service.status(user).get("session_id") or "")
    if not session_id:
        raise RuntimeError("当前没有机构浏览器会话")
    session = institutional_access_service.status(user, session_id)
    if session.get("status") != "active":
        raise RuntimeError("机构登录已失效，请重新连接并完成登录后再下载")
    try:
        await browser_worker_client.status(session_id)
    except Exception as exc:
        institutional_access_service.mark_browser_unavailable(user, session_id, str(exc))
        raise RuntimeError("机构浏览器已经关闭，请重新连接并完成登录后再下载") from exc
    result = await browser_worker_client.download_cnki(session_id, indexes[:5])
    papers = [
        await institutional_access_service.ingest_browser_download(user, session_id, item)
        for item in result.get("items", [])
    ]
    return {"items": papers, "indexes": indexes[:5], "browser": result}


@scholar_tool(
    name="verify_institution_access",
    description="Verify VPN or institution access against a user-provided publisher URL",
    category="institutional_access",
    safety_level=SafetyLevel.LOW,
)
async def verify_institution_access(
    tenant_id: str,
    user_id: str,
    probe_url: str,
    session_id: str = "",
) -> dict[str, Any]:
    user = UserContext(tenant_id=tenant_id, user_id=user_id)
    if not session_id:
        session_id = str(institutional_access_service.status(user).get("session_id") or "")
    if not session_id:
        raise RuntimeError("当前没有机构会话，请先启动机构登录")
    return {"session": await institutional_access_service.verify(user, session_id, probe_url)}


@scholar_tool(
    name="prepare_institution_download",
    description="Create a tenant-scoped institution document download plan",
    category="institutional_access",
    safety_level=SafetyLevel.MEDIUM,
)
async def prepare_institution_download(
    tenant_id: str,
    user_id: str,
    source_url: str,
    session_id: str = "",
    title: str = "机构文献",
    doi: str = "",
    source: str = "institution",
    conversation_id: str = "",
) -> dict[str, Any]:
    user = UserContext(tenant_id=tenant_id, user_id=user_id)
    if not session_id:
        session_id = str(institutional_access_service.status(user).get("session_id") or "")
    return {
        "download": institutional_access_service.prepare_download(
            user,
            session_id=session_id,
            source_url=source_url,
            title=title,
            doi=doi,
            source=source,
            conversation_id=conversation_id,
        )
    }


@scholar_tool(
    name="download_institution_url",
    description="Confirm, download, validate, and ingest one institution PDF or CAJ URL",
    category="institutional_access",
    safety_level=SafetyLevel.HIGH,
)
async def download_institution_url(
    tenant_id: str,
    user_id: str,
    source_url: str,
    title: str = "机构文献",
    doi: str = "",
    source: str = "institution",
    conversation_id: str = "",
    session_id: str = "",
    confirmation_token: str = "",
) -> dict[str, Any]:
    user = UserContext(tenant_id=tenant_id, user_id=user_id)
    if not session_id:
        session_id = str(institutional_access_service.status(user).get("session_id") or "")
    if not session_id:
        raise RuntimeError("当前没有有效机构会话，请先连接并验证学校 VPN 或机构入口")
    plan = institutional_access_service.prepare_download(
        user,
        session_id=session_id,
        source_url=source_url,
        title=title,
        doi=doi,
        source=source,
        conversation_id=conversation_id,
    )
    return await institutional_access_service.confirm_download(
        user,
        str(plan["download_id"]),
        confirmation_token=confirmation_token,
    )


@scholar_tool(
    name="download_institution_paper",
    description="Download, validate, parse, and ingest an institution paper after user confirmation",
    category="institutional_access",
    safety_level=SafetyLevel.HIGH,
)
async def download_institution_paper(
    tenant_id: str,
    user_id: str,
    download_id: str,
    confirmation_token: str = "",
) -> dict[str, Any]:
    user = UserContext(tenant_id=tenant_id, user_id=user_id)
    return await institutional_access_service.confirm_download(
        user,
        download_id,
        confirmation_token=confirmation_token,
    )


@scholar_tool(
    name="revoke_institution_session",
    description="Revoke and clear an institution access session",
    category="institutional_access",
    safety_level=SafetyLevel.HIGH,
)
async def revoke_institution_session(
    tenant_id: str,
    user_id: str,
    session_id: str = "",
    confirmation_token: str = "",
) -> dict[str, Any]:
    user = UserContext(tenant_id=tenant_id, user_id=user_id)
    if not session_id:
        session_id = str(institutional_access_service.status(user).get("session_id") or "")
    if not session_id:
        raise RuntimeError("当前没有可断开的机构会话")
    try:
        await browser_worker_client.close(session_id)
    except Exception:
        pass
    return {"session": institutional_access_service.revoke(user, session_id)}


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
