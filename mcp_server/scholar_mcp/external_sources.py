from __future__ import annotations

import json
import hashlib
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
from html import unescape
from typing import Any

from app.config import get_settings
from mcp_server.scholar_mcp.models import PaperRecord


class ExternalSourceError(RuntimeError):
    """Raised when a real external paper source cannot return usable metadata."""


ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _normalize_id(value: str) -> str:
    return value.strip().replace("/", "_").replace(":", "_").replace(" ", "_")


def _paper_id(source: str, value: str) -> str:
    return f"paper:{source}:{_normalize_id(value)}"


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _read_response(request: urllib.request.Request, timeout: float, max_bytes: int | None, *, direct: bool) -> bytes:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if direct else urllib.request.build_opener()
    with opener.open(request, timeout=timeout) as response:
        if response.status >= 400:
            raise ExternalSourceError(f"external source returned HTTP {response.status}")
        if max_bytes is None:
            return response.read()
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(1024 * 128)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ExternalSourceError(f"download exceeded {max_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)


def _should_retry_without_proxy(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "winerror 10061",
            "connection refused",
            "actively refused",
            "read operation timed out",
            "timed out",
            "proxy",
        )
    )


def _http_get_bytes(url: str, accept: str, max_bytes: int | None = None) -> bytes:
    settings = get_settings()
    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "ScholarAgent/0.1 (+tenant-scoped research workflow)",
        },
    )
    try:
        return _read_response(request, settings.external_source_timeout_seconds, max_bytes, direct=False)
    except ExternalSourceError:
        raise
    except Exception as exc:  # pragma: no cover - network state varies by environment
        if _should_retry_without_proxy(exc):
            try:
                return _read_response(request, settings.external_source_timeout_seconds, max_bytes, direct=True)
            except ExternalSourceError:
                raise
            except Exception as direct_exc:
                raise ExternalSourceError(
                    f"external source unavailable via proxy and direct: proxy={exc}; direct={direct_exc}"
                ) from direct_exc
        raise ExternalSourceError(f"external source unavailable: {exc}") from exc


def _looks_like_arxiv_id(value: str) -> bool:
    raw = value.strip().removeprefix("arXiv:").split("/")[-1]
    return bool(
        re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", raw)
        or re.match(r"^[a-z\-]+(\.[A-Z]{2})?/\d{7}(v\d+)?$", raw, re.IGNORECASE)
    )


def _arxiv_id(value: str) -> str:
    return value.strip().removeprefix("arXiv:").split("/")[-1]


def _safe_filename(name: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in name.strip())
    return cleaned or "paper.pdf"


def _tenant_upload_dir(tenant_id: str, user_id: str) -> Path:
    root = get_settings().storage_dir / "uploads" / tenant_id / user_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def _browser_candidates() -> list[str]:
    candidates = [
        shutil.which("msedge"),
        shutil.which("chrome"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    for path in (
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ):
        if Path(path).exists():
            candidates.append(path)
    return [str(path) for path in candidates if path]


def _print_url_to_pdf(url: str, output_path: Path, timeout: float) -> None:
    browsers = _browser_candidates()
    if not browsers:
        raise ExternalSourceError("no Edge/Chrome browser available for print-to-PDF fallback")

    output_path = output_path.resolve()
    errors: list[str] = []
    browser_temp_root = get_settings().storage_dir / "browser-print"
    browser_temp_root.mkdir(parents=True, exist_ok=True)
    temp_root = tempfile.mkdtemp(prefix="scholar-print-", dir=str(browser_temp_root))
    try:
        temp_dir = Path(temp_root)
        profile_dir = temp_dir
        temp_output = temp_dir / "printed.pdf"
        for browser in browsers:
            for headless_flag in ("--headless=new", "--headless"):
                command = [
                    browser,
                    headless_flag,
                    "--disable-gpu",
                    "--disable-extensions",
                    "--disable-crash-reporter",
                    "--disable-crashpad",
                    "--disable-breakpad",
                    "--disable-features=Crashpad",
                    "--no-first-run",
                    "--no-sandbox",
                    f"--crash-dumps-dir={profile_dir}",
                    f"--user-data-dir={profile_dir}",
                    f"--print-to-pdf={temp_output}",
                    url,
                ]
                try:
                    subprocess.run(command, check=True, timeout=max(10.0, timeout + 5.0), capture_output=True)
                    output_path.write_bytes(temp_output.read_bytes())
                    errors = []
                    break
                except subprocess.TimeoutExpired:
                    errors.append(f"{Path(browser).name} {headless_flag}: timed out")
                except subprocess.CalledProcessError as exc:
                    stderr = (exc.stderr or b"").decode("utf-8", errors="ignore").strip()
                    errors.append(f"{Path(browser).name} {headless_flag}: exit {exc.returncode} {stderr[:300]}")
                except Exception as exc:
                    errors.append(f"{Path(browser).name} {headless_flag}: {exc}")
            if not errors:
                break
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
    if errors:
        raise ExternalSourceError("print-to-PDF fallback failed: " + " | ".join(errors))
    if not output_path.exists() or output_path.stat().st_size < 200:
        raise ExternalSourceError("print-to-PDF fallback produced no usable file")
    if not output_path.read_bytes()[:5].startswith(b"%PDF"):
        raise ExternalSourceError("print-to-PDF fallback did not produce a PDF")


def _extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()[:50000]
    except Exception:
        return ""


def _arxiv_pdf_candidates(arxiv_id: str) -> list[str]:
    quoted = urllib.parse.quote(arxiv_id, safe="/.")
    return [
        f"https://arxiv.org/pdf/{quoted}",
        f"https://export.arxiv.org/pdf/{quoted}",
    ]


def attach_paper_pdf(
    paper: PaperRecord,
    pdf_urls: list[str],
    *,
    max_bytes: int = 50 * 1024 * 1024,
    required: bool = False,
) -> PaperRecord:
    """Download a PDF into tenant storage and attach preview metadata."""
    if not pdf_urls:
        return paper
    if paper.metadata.get("file_path"):
        return paper

    last_error = ""
    settings = get_settings()
    for pdf_url in pdf_urls:
        if not pdf_url:
            continue
        suffix = paper.arxiv_id or paper.doi or hashlib.sha1(pdf_url.encode("utf-8")).hexdigest()[:16]
        filename = _safe_filename(f"{paper.source}_{suffix}.pdf")
        path = _tenant_upload_dir(paper.tenant_id, paper.user_id) / filename
        try:
            raw = _http_get_bytes(pdf_url, "application/pdf", max_bytes=max_bytes)
            if not raw.startswith(b"%PDF"):
                raise ExternalSourceError("downloaded content is not a PDF")
            path.write_bytes(raw)
            extracted_text = _extract_pdf_text(path)
            paper.full_text = paper.full_text or extracted_text
            if extracted_text and (not paper.abstract or len(paper.abstract) < 120):
                paper.abstract = extracted_text[:900]
            paper.metadata = {
                **paper.metadata,
                "pdf_downloaded": True,
                "pdf_url": pdf_url,
                "file_name": filename,
                "file_path": str(path),
                "file_url": f"/knowledge/files/{paper.paper_id}",
                "content_type": "application/pdf",
                "content_length": len(raw),
                "pdf_text_extracted": bool(extracted_text),
            }
            return paper
        except ExternalSourceError as exc:
            last_error = str(exc)
            try:
                _print_url_to_pdf(pdf_url, path, settings.external_source_timeout_seconds)
                extracted_text = _extract_pdf_text(path)
                paper.full_text = paper.full_text or extracted_text
                if extracted_text and (not paper.abstract or len(paper.abstract) < 120):
                    paper.abstract = extracted_text[:900]
                paper.metadata = {
                    **paper.metadata,
                    "pdf_downloaded": True,
                    "pdf_url": pdf_url,
                    "file_name": filename,
                    "file_path": str(path),
                    "file_url": f"/knowledge/files/{paper.paper_id}",
                    "content_type": "application/pdf",
                    "content_length": path.stat().st_size,
                    "pdf_text_extracted": bool(extracted_text),
                    "pdf_capture_method": "browser_print",
                }
                return paper
            except ExternalSourceError as print_exc:
                last_error = f"{last_error}; print fallback: {print_exc}"
        except Exception as exc:  # pragma: no cover - filesystem/network state varies
            last_error = str(exc)

    paper.metadata = {
        **paper.metadata,
        "pdf_downloaded": False,
        "pdf_download_error": last_error or "PDF download failed",
        "pdf_url": pdf_urls[0],
    }
    if required:
        raise ExternalSourceError(paper.metadata["pdf_download_error"])
    return paper


def attach_arxiv_pdf(
    paper: PaperRecord,
    *,
    max_bytes: int = 50 * 1024 * 1024,
    required: bool = False,
) -> PaperRecord:
    """Download an arXiv PDF into tenant storage and attach preview metadata."""
    if paper.source != "arxiv" or not paper.arxiv_id:
        return paper
    return attach_paper_pdf(
        paper,
        _arxiv_pdf_candidates(paper.arxiv_id),
        max_bytes=max_bytes,
        required=required,
    )


def _entry_to_paper(entry: ET.Element, tenant_id: str, user_id: str) -> PaperRecord:
    id_url = _clean_text(entry.findtext("atom:id", default="", namespaces=ATOM_NS))
    arxiv_id = id_url.rstrip("/").split("/")[-1] if id_url else ""
    title = _clean_text(entry.findtext("atom:title", default="", namespaces=ATOM_NS))
    abstract = _clean_text(entry.findtext("atom:summary", default="", namespaces=ATOM_NS))
    authors = [
        _clean_text(author.findtext("atom:name", default="", namespaces=ATOM_NS))
        for author in entry.findall("atom:author", ATOM_NS)
    ]
    authors = [author for author in authors if author]
    published = _clean_text(entry.findtext("atom:published", default="", namespaces=ATOM_NS))[:10] or None
    doi = _clean_text(entry.findtext("arxiv:doi", default="", namespaces=ATOM_NS)) or None
    if not arxiv_id or not title:
        raise ExternalSourceError("arXiv response did not include a usable paper record")
    return PaperRecord(
        paper_id=_paper_id("arxiv", arxiv_id),
        tenant_id=tenant_id,
        user_id=user_id,
        source="arxiv",
        title=title,
        authors=authors,
        abstract=abstract,
        published_at=published,
        doi=doi,
        arxiv_id=arxiv_id,
        url=id_url or f"https://arxiv.org/abs/{urllib.parse.quote(arxiv_id)}",
        metadata={
            "external_source": "arxiv",
            "mock": False,
            "pdf_url": f"https://arxiv.org/pdf/{urllib.parse.quote(arxiv_id, safe='/.')}",
        },
    )


def fetch_arxiv_paper(tenant_id: str, user_id: str, value: str, topic: str = "") -> PaperRecord:
    raw = value.strip() or topic.strip()
    params: dict[str, str]
    if _looks_like_arxiv_id(raw):
        params = {"id_list": _arxiv_id(raw), "start": "0", "max_results": "1"}
    else:
        query = raw or topic
        params = {"search_query": f"all:{query}", "start": "0", "max_results": "1"}
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    data = _http_get_bytes(url, "application/atom+xml")
    root = ET.fromstring(data)
    entry = root.find("atom:entry", ATOM_NS)
    if entry is None:
        raise ExternalSourceError(f"arXiv returned no result for {raw!r}")
    return attach_arxiv_pdf(_entry_to_paper(entry, tenant_id, user_id))


def search_arxiv_papers(
    tenant_id: str,
    user_id: str,
    query: str,
    limit: int,
) -> list[PaperRecord]:
    query = query.strip()
    if not query:
        return []
    params = {
        "search_query": f"all:{query}",
        "start": "0",
        "max_results": str(max(1, min(limit, 50))),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    data = _http_get_bytes(url, "application/atom+xml")
    root = ET.fromstring(data)
    papers: list[PaperRecord] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        try:
            papers.append(_entry_to_paper(entry, tenant_id, user_id))
        except ExternalSourceError:
            continue
    return papers


def _openalex_abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    positioned: list[tuple[int, str]] = []
    for word, positions in index.items():
        for position in positions:
            positioned.append((int(position), word))
    return _clean_text(" ".join(word for _, word in sorted(positioned)))


def _openalex_authors(work: dict[str, Any]) -> list[str]:
    authors = []
    for item in work.get("authorships") or []:
        name = _clean_text(((item.get("author") or {}).get("display_name")))
        if name:
            authors.append(name)
    return authors


def _openalex_work_to_paper(work: dict[str, Any], tenant_id: str, user_id: str) -> PaperRecord | None:
    work_id = _clean_text(work.get("id"))
    title = _clean_text(work.get("title") or work.get("display_name"))
    if not work_id or not title:
        return None
    doi = _clean_text(work.get("doi")) or None
    stable_id = (doi or work_id.rstrip("/").split("/")[-1]).removeprefix("https://doi.org/")
    oa_location = work.get("best_oa_location") or {}
    pdf_url = _clean_text(oa_location.get("pdf_url")) or None
    landing_url = _clean_text(oa_location.get("landing_page_url")) or None
    paper = PaperRecord(
        paper_id=_paper_id("openalex", stable_id),
        tenant_id=tenant_id,
        user_id=user_id,
        source="openalex",
        title=title,
        authors=_openalex_authors(work),
        abstract=_openalex_abstract(work.get("abstract_inverted_index")),
        published_at=_clean_text(work.get("publication_date")) or None,
        doi=stable_id if doi else None,
        url=doi or landing_url or work_id,
        metadata={
            "external_source": "openalex",
            "mock": False,
            "openalex_id": work_id,
            "pdf_url": pdf_url,
            "landing_page_url": landing_url,
            "is_oa": bool(work.get("open_access", {}).get("is_oa")),
            "cited_by_count": int(work.get("cited_by_count") or 0),
        },
    )
    paper = attach_paper_pdf(paper, [url for url in (pdf_url, landing_url, paper.url) if url])
    return paper


def search_openalex_papers(
    tenant_id: str,
    user_id: str,
    query: str,
    limit: int,
) -> list[PaperRecord]:
    query = query.strip()
    if not query:
        return []
    params = {
        "search": query,
        "per-page": str(max(1, min(limit, 50))),
        "sort": "relevance_score:desc",
    }
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    data = json.loads(_http_get_bytes(url, "application/json").decode("utf-8"))
    papers: list[PaperRecord] = []
    for work in data.get("results") or []:
        paper = _openalex_work_to_paper(work, tenant_id, user_id)
        if paper is not None:
            papers.append(paper)
    return papers


def _crossref_date(message: dict[str, Any]) -> str | None:
    for key in ("published-print", "published-online", "published", "issued"):
        parts = (message.get(key) or {}).get("date-parts") or []
        if not parts or not parts[0]:
            continue
        values = [str(item).zfill(2) if index else str(item) for index, item in enumerate(parts[0][:3])]
        return "-".join(values)
    return None


def _crossref_authors(message: dict[str, Any]) -> list[str]:
    authors = []
    for item in message.get("author") or []:
        given = _clean_text(item.get("given"))
        family = _clean_text(item.get("family"))
        name = _clean_text(" ".join(part for part in (given, family) if part))
        if name:
            authors.append(name)
    return authors


def fetch_crossref_paper(tenant_id: str, user_id: str, doi: str, topic: str = "") -> PaperRecord:
    value = doi.strip()
    if not value:
        raise ExternalSourceError("DOI is required for Crossref lookup")
    url = "https://api.crossref.org/works/" + urllib.parse.quote(value, safe="")
    data = json.loads(_http_get_bytes(url, "application/json").decode("utf-8"))
    message = data.get("message") or {}
    titles = message.get("title") or []
    title = _clean_text(titles[0] if titles else topic or value)
    abstract = _clean_text(re.sub(r"<[^>]+>", " ", unescape(message.get("abstract") or "")))
    canonical_doi = _clean_text(message.get("DOI")) or value
    if not title:
        raise ExternalSourceError(f"Crossref returned no title for DOI {value!r}")
    paper = PaperRecord(
        paper_id=_paper_id("doi", canonical_doi),
        tenant_id=tenant_id,
        user_id=user_id,
        source="doi",
        title=title,
        authors=_crossref_authors(message),
        abstract=abstract,
        published_at=_crossref_date(message),
        doi=canonical_doi,
        url=_clean_text(message.get("URL")) or f"https://doi.org/{urllib.parse.quote(canonical_doi)}",
        metadata={"external_source": "crossref", "mock": False, "type": message.get("type")},
    )
    return attach_paper_pdf(paper, [paper.url] if paper.url else [])


def _crossref_work_to_paper(message: dict[str, Any], tenant_id: str, user_id: str, topic: str = "") -> PaperRecord | None:
    titles = message.get("title") or []
    title = _clean_text(titles[0] if titles else topic)
    canonical_doi = _clean_text(message.get("DOI"))
    if not title or not canonical_doi:
        return None
    abstract = _clean_text(re.sub(r"<[^>]+>", " ", unescape(message.get("abstract") or "")))
    paper = PaperRecord(
        paper_id=_paper_id("doi", canonical_doi),
        tenant_id=tenant_id,
        user_id=user_id,
        source="doi",
        title=title,
        authors=_crossref_authors(message),
        abstract=abstract,
        published_at=_crossref_date(message),
        doi=canonical_doi,
        url=_clean_text(message.get("URL")) or f"https://doi.org/{urllib.parse.quote(canonical_doi)}",
        metadata={
            "external_source": "crossref",
            "mock": False,
            "type": message.get("type"),
            "is_referenced_by_count": int(message.get("is-referenced-by-count") or 0),
        },
    )
    return attach_paper_pdf(paper, [paper.url] if paper.url else [])


def search_crossref_papers(
    tenant_id: str,
    user_id: str,
    query: str,
    limit: int,
) -> list[PaperRecord]:
    query = query.strip()
    if not query:
        return []
    params = {
        "query.bibliographic": query,
        "rows": str(max(1, min(limit, 50))),
        "sort": "relevance",
        "order": "desc",
    }
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    data = json.loads(_http_get_bytes(url, "application/json").decode("utf-8"))
    papers: list[PaperRecord] = []
    for item in (data.get("message") or {}).get("items") or []:
        paper = _crossref_work_to_paper(item, tenant_id, user_id, query)
        if paper is not None:
            papers.append(paper)
    return papers
