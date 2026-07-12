from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.dependencies import AuthError, authenticate_api_key
from app.services import mysql_store
from app.services.rag_service import rag_service
from mcp_server.scholar_mcp.client import ScholarMCPClient

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _current_user(x_api_key: str | None):
    try:
        return authenticate_api_key(x_api_key)
    except AuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


class KnowledgePaperDTO(BaseModel):
    paper_id: str | None = Field(default=None, max_length=260)
    source: str = Field(default="manual", max_length=40)
    title: str = Field(..., min_length=1, max_length=500)
    authors: list[str] = Field(default_factory=list)
    abstract: str = Field(default="", max_length=8000)
    full_text: str = Field(default="", max_length=50000)
    published_at: str | None = Field(default=None, max_length=40)
    doi: str | None = Field(default=None, max_length=200)
    arxiv_id: str | None = Field(default=None, max_length=120)
    url: str | None = Field(default=None, max_length=500)
    in_knowledge_base: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


def _stable_paper_id(source: str, title: str) -> str:
    digest = hashlib.sha1(f"{source}:{title}".encode("utf-8")).hexdigest()[:12]
    return f"paper:{source}:{digest}"


def _safe_filename(name: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in name.strip())
    return cleaned or "paper.bin"


def _extract_docx_text(path: Path) -> str:
    try:
        with ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
    except Exception:
        return ""
    try:
        root = ET.fromstring(xml)
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for paragraph in root.findall(".//w:p", namespace):
            texts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
            if "".join(texts).strip():
                paragraphs.append("".join(texts))
        return "\n".join(paragraphs).strip()[:50000]
    except Exception:
        return ""


def _extract_uploaded_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown", ".html", ".htm"}:
        return path.read_text(encoding="utf-8", errors="ignore")[:50000]
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError:
            return ""
        try:
            reader = PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages).strip()[:50000]
        except Exception:
            return ""
    if suffix == ".docx":
        return _extract_docx_text(path)
    return ""


def _media_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "application/pdf"
    if suffix in {".md", ".markdown"}:
        return "text/markdown; charset=utf-8"
    if suffix in {".txt"}:
        return "text/plain; charset=utf-8"
    if suffix in {".html", ".htm"}:
        return "text/html; charset=utf-8"
    if suffix == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


async def _find_user_paper(paper_id: str, user) -> dict[str, Any]:
    client = ScholarMCPClient()
    result = await client.call_tool(
        "search_papers",
        {
            "tenant_id": user.tenant_id,
            "user_id": user.user_id,
            "query": paper_id,
            "source": "local",
            "limit": 5,
        },
    )
    paper = next((item for item in result["items"] if item.get("paper_id") == paper_id), None)
    if paper is None:
        raise HTTPException(status_code=404, detail="paper not found")
    return paper


def _resolve_tenant_file(file_path: str, user) -> Path:
    resolved = Path(file_path).resolve()
    upload_root = (get_settings().storage_dir / "uploads" / user.tenant_id / user.user_id).resolve()
    if upload_root not in resolved.parents:
        raise HTTPException(status_code=403, detail="file is outside tenant storage")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return resolved


def _reject_unconverted_caj(path: Path) -> None:
    if path.suffix.lower() == ".caj":
        raise HTTPException(
            status_code=409,
            detail="该历史文件尚未转换为 PDF，系统不会直接输出或下载 CAJ",
        )


class FileAnnotationDTO(BaseModel):
    strokes: list[dict[str, Any]] = Field(default_factory=list)
    notes: str = Field(default="", max_length=50000)


class FileTextUpdateDTO(BaseModel):
    content: str = Field(..., min_length=1, max_length=50000)


@router.get("/recent")
async def recent_knowledge(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> dict[str, Any]:
    user = _current_user(x_api_key)
    client = ScholarMCPClient()
    result = await client.call_tool(
        "search_papers",
        {
            "tenant_id": user.tenant_id,
            "user_id": user.user_id,
            "query": "",
            "source": "local",
            "limit": 20,
        },
    )
    return {"items": result["items"]}


@router.get("")
async def list_knowledge(
    query: str = "",
    source: str = "local",
    limit: int = Query(default=50, ge=1, le=200),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    client = ScholarMCPClient()
    result = await client.call_tool(
        "search_papers",
        {
            "tenant_id": user.tenant_id,
            "user_id": user.user_id,
            "query": query,
            "source": source,
            "limit": limit,
        },
    )
    return {
        "items": result["items"],
        "has_more": result.get("has_more", False),
        "external_error": result.get("external_error"),
    }


@router.post("")
async def save_knowledge(
    request: KnowledgePaperDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    paper = getattr(request, "model_dump", request.dict)()
    paper["paper_id"] = paper["paper_id"] or _stable_paper_id(paper["source"], paper["title"])
    client = ScholarMCPClient()
    result = await client.call_tool(
        "save_to_knowledge",
        {
            "tenant_id": user.tenant_id,
            "user_id": user.user_id,
            "paper": paper,
        },
    )
    stats = await rag_service.stats(user.tenant_id, user.user_id)
    return {"item": result["paper"], "rag": stats}


@router.post("/upload")
async def upload_knowledge_file(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    authors: str = Form(default=""),
    source: str = Form(default="pdf"),
    published_at: str = Form(default=""),
    doi: str = Form(default=""),
    arxiv_id: str = Form(default=""),
    url: str = Form(default=""),
    abstract: str = Form(default=""),
    in_knowledge_base: bool = Form(default=True),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    # Unwrap Form() default when called directly (not through FastAPI)
    if not isinstance(in_knowledge_base, bool):
        in_knowledge_base = True
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty file")
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large")

    digest = hashlib.sha1(raw).hexdigest()[:12]
    safe_name = _safe_filename(file.filename or f"{digest}.bin")
    suffix = Path(safe_name).suffix.lower()
    normalized_source = "pdf" if suffix == ".pdf" else ("docx" if suffix == ".docx" else (source or "manual"))
    paper_id = f"paper:{normalized_source}:{digest}"
    upload_dir = get_settings().storage_dir / "uploads" / user.tenant_id / user.user_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_path = upload_dir / f"{digest}_{safe_name}"
    stored_path.write_bytes(raw)

    full_text = _extract_uploaded_text(stored_path)
    paper_title = title.strip() or Path(safe_name).stem
    paper = KnowledgePaperDTO(
        paper_id=paper_id,
        source=normalized_source,
        title=paper_title,
        authors=[item.strip() for item in authors.split(",") if item.strip()],
        abstract=abstract.strip() or full_text[:900],
        full_text=full_text,
        published_at=published_at.strip() or None,
        doi=doi.strip() or None,
        arxiv_id=arxiv_id.strip() or None,
        url=url.strip() or None,
        in_knowledge_base=in_knowledge_base,
        metadata={
            "created_from": "web_upload",
            "file_name": safe_name,
            "file_path": str(stored_path),
            "file_url": f"/knowledge/files/{paper_id}",
            "content_type": file.content_type or "application/octet-stream",
            "content_length": len(raw),
        },
    )
    return await save_knowledge(paper, x_api_key=x_api_key)


@router.get("/files/{paper_id}")
async def get_knowledge_file(
    paper_id: str,
    api_key: str = "",
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> FileResponse:
    user = _current_user(x_api_key or api_key)
    paper = await _find_user_paper(paper_id, user)
    file_path = paper.get("metadata", {}).get("file_path")
    if not file_path:
        raise HTTPException(status_code=404, detail="file not found")
    resolved = _resolve_tenant_file(file_path, user)
    _reject_unconverted_caj(resolved)
    return FileResponse(
        resolved,
        media_type=_media_type_for(resolved),
        filename=resolved.name,
        content_disposition_type="inline",
    )

@router.get("/files/{paper_id}/pdf-info")
async def get_pdf_info(
    paper_id: str,
    api_key: str = "",
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key or api_key)
    paper = await _find_user_paper(paper_id, user)
    file_path = paper.get("metadata", {}).get("file_path")
    if not file_path:
        raise HTTPException(status_code=404, detail="file not found for this paper")
    resolved = _resolve_tenant_file(file_path, user)
    _reject_unconverted_caj(resolved)
    pages = 0
    if resolved.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(resolved))
            pages = len(reader.pages)
        except Exception:
            pages = 0
    return {
        "paper_id": paper_id,
        "pages": pages,
        "file_size": resolved.stat().st_size,
        "file_name": resolved.name,
    }


@router.get("/files/{paper_id}/annotations")
async def get_file_annotations(
    paper_id: str,
    api_key: str = "",
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key or api_key)
    await _find_user_paper(paper_id, user)
    annotations = mysql_store.get_annotations(user.tenant_id, user.user_id, paper_id)
    # Convert back to old API format for backward compatibility
    strokes: list[dict[str, Any]] = []
    notes_parts: list[str] = []
    for ann in annotations:
        if ann["annotation_type"] == "note":
            if ann["content"]:
                notes_parts.append(ann["content"])
        else:
            strokes.append({
                "page": ann["page"],
                "type": ann["annotation_type"],
                "color": ann.get("color"),
                "points": ann.get("points", []),
                "content": ann.get("content", ""),
            })
    return {
        "paper_id": paper_id,
        "strokes": strokes,
        "notes": "\n".join(notes_parts),
    }


@router.post("/files/{paper_id}/annotations")
async def save_file_annotations(
    paper_id: str,
    request: FileAnnotationDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    await _find_user_paper(paper_id, user)
    # Convert old DTO format to annotation rows
    annotations: list[dict[str, Any]] = []
    for stroke in request.strokes[:1000]:
        annotations.append({
            "page": stroke.get("page", 0),
            "annotation_type": stroke.get("type", "highlight"),
            "color": stroke.get("color"),
            "points": stroke.get("points", []),
            "content": stroke.get("content", ""),
        })
    if request.notes:
        annotations.append({
            "page": 0,
            "annotation_type": "note",
            "color": None,
            "points": [],
            "content": request.notes,
        })
    count = mysql_store.save_annotations(user.tenant_id, user.user_id, paper_id, annotations)
    return {
        "saved": True,
        "paper_id": paper_id,
        "count": count,
        "strokes": request.strokes,
        "notes": request.notes,
    }


@router.put("/files/{paper_id}/text")
async def save_file_text(
    paper_id: str,
    request: FileTextUpdateDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    paper = await _find_user_paper(paper_id, user)
    file_path = paper.get("metadata", {}).get("file_path")
    if not file_path:
        raise HTTPException(status_code=404, detail="file not found")
    resolved = _resolve_tenant_file(file_path, user)
    if resolved.suffix.lower() not in {".txt", ".md", ".markdown", ".html", ".htm"}:
        raise HTTPException(status_code=400, detail="only text-like files can be edited inline")
    resolved.write_text(request.content, encoding="utf-8")
    paper_fields = {
        "paper_id",
        "source",
        "title",
        "authors",
        "abstract",
        "full_text",
        "published_at",
        "doi",
        "arxiv_id",
        "url",
        "metadata",
        "in_knowledge_base",
        "file_path",
    }
    updated = {key: paper.get(key) for key in paper_fields if key in paper}
    updated["full_text"] = request.content
    updated["abstract"] = updated.get("abstract") or request.content[:900]
    updated["metadata"] = {**(updated.get("metadata") or {}), "updated_from": "inline_file_editor"}
    client = ScholarMCPClient()
    result = await client.call_tool(
        "save_to_knowledge",
        {
            "tenant_id": user.tenant_id,
            "user_id": user.user_id,
            "paper": updated,
        },
    )
    stats = await rag_service.stats(user.tenant_id, user.user_id)
    return {"saved": True, "item": result["paper"], "rag": stats}


@router.delete("/{paper_id}")
async def delete_knowledge(
    paper_id: str,
    confirmation_token: str = "",
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    client = ScholarMCPClient()
    result = await client.call_tool(
        "delete_knowledge",
        {
            "tenant_id": user.tenant_id,
            "user_id": user.user_id,
            "paper_id": paper_id,
            "confirmation_token": confirmation_token,
        },
    )
    return result


class ToggleKbDTO(BaseModel):
    in_knowledge_base: bool


@router.put("/{paper_id}/toggle-kb")
async def toggle_knowledge_base(
    paper_id: str,
    request: ToggleKbDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    await _find_user_paper(paper_id, user)
    from mcp_server.scholar_mcp.tools import toggle_knowledge_base as _toggle_kb
    result = await _toggle_kb(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        paper_id=paper_id,
        in_knowledge_base=request.in_knowledge_base,
    )
    stats = await rag_service.stats(user.tenant_id, user.user_id)
    return {**result, "rag": stats}


@router.get("/rag/search")
async def search_rag(
    query: str = "",
    limit: int = Query(default=10, ge=1, le=50),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    return await rag_service.search(user.tenant_id, user.user_id, query, limit)


@router.get("/rag/stats")
async def rag_stats(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> dict[str, Any]:
    user = _current_user(x_api_key)
    return await rag_service.stats(user.tenant_id, user.user_id)
