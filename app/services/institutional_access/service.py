from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import socket
import urllib.parse
import urllib.request
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.schemas import UserContext
from app.services.institutional_access.store import institutional_access_store


class InstitutionalAccessError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned[:120] or "institution_document"


def _convert_caj_to_pdf(source: Path, destination: Path) -> bool:
    executable = Path(sys.executable).parent / "caj2pdf.exe"
    if not executable.exists():
        return False
    completed = subprocess.run(
        [str(executable), "convert", str(source), "-o", str(destination), "-m", "MUTOOL"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0 or not destination.exists():
        destination.unlink(missing_ok=True)
        return False
    raw = destination.read_bytes()
    if not raw.startswith(b"%PDF-") or len(raw) < 1024:
        destination.unlink(missing_ok=True)
        return False
    return True


def _validate_url_syntax(value: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise InstitutionalAccessError("INVALID_SOURCE_URL", "仅支持有效的 HTTP/HTTPS 文献地址")
    return parsed


def _validate_public_url(value: str) -> urllib.parse.ParseResult:
    parsed = _validate_url_syntax(value)
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise InstitutionalAccessError("SOURCE_URL_BLOCKED", "不允许访问本机或内网地址")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443)}
    except socket.gaierror as exc:
        raise InstitutionalAccessError("SOURCE_UNREACHABLE", f"无法解析文献站点：{host}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise InstitutionalAccessError("SOURCE_URL_BLOCKED", "不允许访问本机或内网地址")
    return parsed


def _should_retry_without_proxy(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "winerror 10061",
            "connection refused",
            "actively refused",
            "积极拒绝",
            "proxy",
            "timed out",
            "timeout",
        )
    )


def _read_request(
    request: urllib.request.Request,
    *,
    max_bytes: int,
    timeout: float,
    direct: bool,
) -> tuple[bytes, str, str]:
    opener = (
        urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if direct
        else urllib.request.build_opener()
    )
    with opener.open(request, timeout=timeout) as response:
        final_url = response.geturl()
        _validate_public_url(final_url)
        content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
        raw = response.read(max_bytes + 1)
    return raw, content_type, final_url


def _request_bytes(url: str, *, max_bytes: int, timeout: float) -> tuple[bytes, str, str]:
    _validate_public_url(url)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 ScholarAgent/0.1 institutional-access",
            "Accept": "application/pdf,application/octet-stream,text/html;q=0.5,*/*;q=0.1",
        },
    )
    try:
        raw, content_type, final_url = _read_request(
            request, max_bytes=max_bytes, timeout=timeout, direct=False
        )
    except Exception as proxy_exc:
        if not _should_retry_without_proxy(proxy_exc):
            raise
        try:
            raw, content_type, final_url = _read_request(
                request, max_bytes=max_bytes, timeout=timeout, direct=True
            )
        except Exception as direct_exc:
            raise InstitutionalAccessError(
                "SOURCE_UNREACHABLE",
                "机构站点通过系统代理和直连均无法访问。请检查校园网/VPN、机构入口和本机代理设置。"
                f" 直连错误：{direct_exc}",
            ) from direct_exc
    if len(raw) > max_bytes:
        raise InstitutionalAccessError("DOWNLOAD_TOO_LARGE", "文献文件超过当前租户允许的大小")
    return raw, content_type, final_url


def _detect_document(raw: bytes, content_type: str, source_url: str) -> str:
    prefix = raw[:1024].lstrip()
    lowered = prefix.lower()
    if raw.startswith(b"%PDF-"):
        return "pdf"
    if raw.startswith((b"CAJViewer", b"CAJ\x00", b"HZKJ")) or source_url.lower().endswith(".caj"):
        if b"<html" not in lowered and b"<!doctype" not in lowered:
            return "caj"
    if "text/html" in content_type or b"<html" in lowered or b"<!doctype" in lowered:
        text = raw[:8192].decode("utf-8", errors="ignore").lower()
        if any(marker in text for marker in ("登录", "login", "captcha", "验证码", "无权", "购买")):
            raise InstitutionalAccessError("USER_LOGIN_REQUIRED", "站点返回了登录或权限页面，请重新完成机构认证")
        raise InstitutionalAccessError("DOWNLOAD_NOT_A_DOCUMENT", "下载地址返回网页而不是 PDF/CAJ 文件")
    raise InstitutionalAccessError("DOCUMENT_FORMAT_UNSUPPORTED", "当前下载内容不是可识别的 PDF 或 CAJ 文件")


def _extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()[:50000]
    except Exception:
        return ""


def _validate_article_pdf(path: Path, source_url: str = "") -> None:
    if "/ads/" in source_url.lower():
        raise InstitutionalAccessError("PDF_PLACEHOLDER", "下载结果是知网占位页，不是论文正文")
    try:
        raw = path.read_bytes()
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = "".join((page.extract_text() or "") for page in reader.pages[:3])
        compact = re.sub(r"\s+", "", text)
        if compact in {"1/1绘制", "1/1", "绘制"}:
            raise InstitutionalAccessError("PDF_PLACEHOLDER", "下载结果是占位 PDF，不是论文正文")
        if len(reader.pages) == 1 and len(raw) < 100 * 1024 and len(compact) < 200:
            raise InstitutionalAccessError("PDF_INCOMPLETE", "下载结果疑似预览页，未达到论文正文校验要求")
    except InstitutionalAccessError:
        raise
    except Exception as exc:
        raise InstitutionalAccessError("PDF_INVALID", f"PDF 正文校验失败：{exc}") from exc


class InstitutionalAccessService:
    allowed_access_types = {"system_vpn", "webvpn", "ezproxy", "publisher_login"}

    def save_profile(
        self,
        user: UserContext,
        *,
        institution_name: str,
        access_type: str,
        login_url: str,
        proxy_prefix: str = "",
        profile_id: str = "",
    ) -> dict[str, Any]:
        if access_type not in self.allowed_access_types:
            raise InstitutionalAccessError("INVALID_ACCESS_TYPE", "不支持的机构访问方式")
        _validate_url_syntax(login_url)
        if proxy_prefix:
            _validate_url_syntax(proxy_prefix)
        return institutional_access_store.save_profile(
            user,
            institution_name=institution_name.strip(),
            access_type=access_type,
            login_url=login_url.strip(),
            proxy_prefix=proxy_prefix.strip(),
            profile_id=profile_id,
        )

    def list_profiles(self, user: UserContext) -> list[dict[str, Any]]:
        return institutional_access_store.list_profiles(user)

    def start_session(self, user: UserContext, profile_id: str) -> dict[str, Any]:
        profile = institutional_access_store.get_profile(user, profile_id)
        if profile is None:
            raise InstitutionalAccessError("PROFILE_NOT_FOUND", "机构配置不存在")
        session = institutional_access_store.create_session(user, profile_id)
        return {
            **session,
            "institution_name": profile["institution_name"],
            "access_type": profile["access_type"],
            "login_url": profile["login_url"],
            "requires_visible_login": profile["access_type"] != "system_vpn",
        }

    def status(self, user: UserContext, session_id: str = "") -> dict[str, Any]:
        session = (
            institutional_access_store.get_session(user, session_id)
            if session_id
            else institutional_access_store.latest_session(user)
        )
        if not session:
            return {"status": "disconnected"}
        expires_at = session.get("expires_at")
        if session.get("status") == "active" and expires_at:
            try:
                if datetime.fromisoformat(str(expires_at)) <= _utcnow():
                    return institutional_access_store.update_session(
                        user,
                        str(session["session_id"]),
                        status="expired",
                        last_error="机构登录会话已过期，请重新连接并完成登录",
                    ) or session
            except ValueError:
                pass
        return session

    def mark_browser_unavailable(
        self, user: UserContext, session_id: str, reason: str = ""
    ) -> dict[str, Any]:
        return institutional_access_store.update_session(
            user,
            session_id,
            status="expired",
            last_error=reason or "机构浏览器已经关闭，请重新连接并完成登录",
        ) or {"status": "expired"}

    async def verify(
        self,
        user: UserContext,
        session_id: str,
        probe_url: str,
    ) -> dict[str, Any]:
        session = institutional_access_store.get_session(user, session_id)
        if session is None:
            raise InstitutionalAccessError("SESSION_NOT_FOUND", "机构会话不存在")
        profile = institutional_access_store.get_profile(user, session["profile_id"])
        if profile is None:
            raise InstitutionalAccessError("PROFILE_NOT_FOUND", "机构配置不存在")
        institutional_access_store.update_session(user, session_id, status="verifying", last_error="")
        try:
            raw, content_type, final_url = await asyncio.to_thread(
                _request_bytes,
                probe_url,
                max_bytes=2 * 1024 * 1024,
                timeout=get_settings().external_source_timeout_seconds,
            )
            if "text/html" in content_type:
                text = raw.decode("utf-8", errors="ignore").lower()
                blocked = any(marker in text for marker in ("请登录", "sign in", "login required", "验证码"))
                if blocked:
                    raise InstitutionalAccessError("USER_LOGIN_REQUIRED", "站点仍要求登录，机构认证尚未生效")
            domain = urllib.parse.urlparse(final_url).hostname or ""
            expires_at = (_utcnow() + timedelta(hours=8)).isoformat()
            return institutional_access_store.update_session(
                user,
                session_id,
                status="active",
                authenticated_domains=[domain],
                verified_at=_utcnow().isoformat(),
                expires_at=expires_at,
                last_error="",
            ) or {}
        except InstitutionalAccessError as exc:
            institutional_access_store.update_session(
                user, session_id, status="awaiting_user_login", last_error=str(exc)
            )
            raise
        except Exception as exc:
            institutional_access_store.update_session(
                user, session_id, status="error", last_error=str(exc)
            )
            raise InstitutionalAccessError("ACCESS_VERIFICATION_FAILED", f"机构访问验证失败：{exc}") from exc

    def revoke(self, user: UserContext, session_id: str) -> dict[str, Any]:
        session = institutional_access_store.get_session(user, session_id)
        if session is None:
            raise InstitutionalAccessError("SESSION_NOT_FOUND", "机构会话不存在")
        return institutional_access_store.update_session(
            user,
            session_id,
            status="revoked",
            revoked_at=_utcnow().isoformat(),
            authenticated_domains=[],
        ) or {}

    def activate_browser_session(
        self,
        user: UserContext,
        session_id: str,
        current_url: str,
    ) -> dict[str, Any]:
        session = institutional_access_store.get_session(user, session_id)
        if session is None:
            raise InstitutionalAccessError("SESSION_NOT_FOUND", "机构会话不存在")
        domain = urllib.parse.urlparse(current_url).hostname or "browser-session"
        return institutional_access_store.update_session(
            user,
            session_id,
            status="active",
            authenticated_domains=[domain],
            verified_at=_utcnow().isoformat(),
            expires_at=(_utcnow() + timedelta(hours=8)).isoformat(),
            last_error="",
        ) or {}

    def prepare_download(
        self,
        user: UserContext,
        *,
        session_id: str,
        source_url: str,
        title: str,
        doi: str = "",
        source: str = "institution",
        conversation_id: str = "",
    ) -> dict[str, Any]:
        session = institutional_access_store.get_session(user, session_id)
        if session is None or session.get("status") != "active":
            raise InstitutionalAccessError("INSTITUTION_SESSION_REQUIRED", "请先完成机构访问验证")
        expires_at = session.get("expires_at")
        if expires_at and datetime.fromisoformat(expires_at) <= _utcnow():
            institutional_access_store.update_session(user, session_id, status="expired")
            raise InstitutionalAccessError("INSTITUTION_SESSION_EXPIRED", "机构会话已经过期，请重新认证")
        _validate_public_url(source_url)
        return institutional_access_store.create_download(
            user,
            session_id=session_id,
            source=source,
            source_url=source_url,
            title=title.strip() or "机构文献",
            doi=doi.strip(),
            conversation_id=conversation_id,
        )

    async def confirm_download(
        self,
        user: UserContext,
        download_id: str,
        *,
        confirmation_token: str,
    ) -> dict[str, Any]:
        download = institutional_access_store.get_download(user, download_id)
        if download is None:
            raise InstitutionalAccessError("DOWNLOAD_NOT_FOUND", "下载任务不存在")
        if not confirmation_token:
            raise InstitutionalAccessError("DOWNLOAD_CONFIRMATION_REQUIRED", "下载前需要用户确认")
        session = institutional_access_store.get_session(user, download["session_id"])
        if session is None or session.get("status") != "active":
            raise InstitutionalAccessError("INSTITUTION_SESSION_EXPIRED", "机构会话不可用，请重新认证")
        institutional_access_store.update_download(user, download_id, status="downloading")
        try:
            raw, content_type, final_url = await asyncio.to_thread(
                _request_bytes,
                download["source_url"],
                max_bytes=50 * 1024 * 1024,
                timeout=max(20.0, get_settings().external_source_timeout_seconds),
            )
            file_type = _detect_document(raw, content_type, final_url)
            digest = hashlib.sha256(raw).hexdigest()
            suffix = f".{file_type}"
            storage = (
                get_settings().storage_dir
                / "uploads"
                / user.tenant_id
                / user.user_id
                / "institutional"
            )
            storage.mkdir(parents=True, exist_ok=True)
            path = storage / f"{digest[:16]}_{_safe_name(download.get('title') or 'paper')}{suffix}"
            path.write_bytes(raw)
            full_text = await asyncio.to_thread(_extract_pdf_text, path) if file_type == "pdf" else ""
            paper_id = f"paper:{download['source']}:{digest[:16]}"
            from mcp_server.scholar_mcp.models import PaperRecord
            from mcp_server.scholar_mcp.store import knowledge_store

            paper = PaperRecord(
                paper_id=paper_id,
                tenant_id=user.tenant_id,
                user_id=user.user_id,
                source=download["source"],
                title=download.get("title") or path.stem,
                abstract=full_text[:900],
                full_text=full_text,
                doi=download.get("doi") or None,
                url=final_url,
                file_path=str(path),
                metadata={
                    "created_from": "institutional_download",
                    "download_id": download_id,
                    "session_id": download["session_id"],
                    "file_name": path.name,
                    "file_path": str(path),
                    "file_url": f"/knowledge/files/{paper_id}",
                    "content_type": content_type,
                    "content_length": len(raw),
                    "file_sha256": digest,
                    "document_format": file_type,
                    "parsed": bool(full_text),
                },
            )
            saved = await knowledge_store.save_paper(paper)
            completed = institutional_access_store.update_download(
                user,
                download_id,
                status="completed",
                file_type=file_type,
                file_path=str(path),
                file_sha256=digest,
                file_size=len(raw),
                paper_id=paper_id,
                completed_at=_utcnow().isoformat(),
                failure_code="",
                failure_message="",
            ) or {}
            return {"download": completed, "paper": saved}
        except InstitutionalAccessError as exc:
            institutional_access_store.update_download(
                user,
                download_id,
                status="failed",
                failure_code=exc.code,
                failure_message=str(exc),
                completed_at=_utcnow().isoformat(),
            )
            raise
        except Exception as exc:
            institutional_access_store.update_download(
                user,
                download_id,
                status="failed",
                failure_code="DOWNLOAD_FAILED",
                failure_message=str(exc),
                completed_at=_utcnow().isoformat(),
            )
            raise InstitutionalAccessError("DOWNLOAD_FAILED", f"机构文献下载失败：{exc}") from exc

    def get_download(self, user: UserContext, download_id: str) -> dict[str, Any]:
        download = institutional_access_store.get_download(user, download_id)
        if download is None:
            raise InstitutionalAccessError("DOWNLOAD_NOT_FOUND", "下载任务不存在")
        return download

    async def ingest_browser_download(
        self,
        user: UserContext,
        session_id: str,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        source_path = Path(str(item.get("file_path") or "")).resolve()
        allowed_root = (
            get_settings().storage_dir
            / "browser-downloads"
            / user.tenant_id
            / user.user_id
        ).resolve()
        if allowed_root not in source_path.parents:
            raise InstitutionalAccessError("DOWNLOAD_PATH_BLOCKED", "浏览器下载文件不在当前租户目录")
        if not source_path.exists() or not source_path.is_file():
            raise InstitutionalAccessError("DOWNLOAD_NOT_FOUND", "浏览器下载文件不存在")
        raw = source_path.read_bytes()
        if len(raw) > 50 * 1024 * 1024:
            raise InstitutionalAccessError("DOWNLOAD_TOO_LARGE", "下载文件超过 50MB 限制")
        file_type = _detect_document(raw, "application/octet-stream", source_path.as_uri())
        digest = hashlib.sha256(raw).hexdigest()
        title = str(item.get("title") or source_path.stem)
        destination_root = (
            get_settings().storage_dir
            / "uploads"
            / user.tenant_id
            / user.user_id
            / "institutional"
        )
        destination_root.mkdir(parents=True, exist_ok=True)
        destination = destination_root / f"{digest[:16]}_{_safe_name(title)}.{file_type}"
        if source_path != destination:
            shutil.copyfile(source_path, destination)
        original_file_path = str(destination) if file_type == "caj" else ""
        if file_type == "caj":
            converted = destination.with_suffix(".pdf")
            converted_ok = await asyncio.to_thread(_convert_caj_to_pdf, destination, converted)
            if converted_ok:
                destination = converted
                raw = destination.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                file_type = "pdf"
        if file_type == "pdf":
            _validate_article_pdf(destination, str(item.get("detail_url") or ""))
        full_text = await asyncio.to_thread(_extract_pdf_text, destination) if file_type == "pdf" else ""
        paper_id = f"paper:cnki:{digest[:16]}"
        from mcp_server.scholar_mcp.models import PaperRecord
        from mcp_server.scholar_mcp.store import knowledge_store

        paper = PaperRecord(
            paper_id=paper_id,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            source="cnki",
            title=title,
            abstract=full_text[:900],
            full_text=full_text,
            url=str(item.get("detail_url") or ""),
            file_path=str(destination),
            metadata={
                "created_from": "browser_worker_cnki_download",
                "session_id": session_id,
                "file_name": destination.name,
                "file_path": str(destination),
                "file_url": f"/knowledge/files/{paper_id}",
                "content_length": len(raw),
                "file_sha256": digest,
                "document_format": file_type,
                "original_file_path": original_file_path,
                "converted_from_caj": bool(original_file_path and file_type == "pdf"),
                "parsed": bool(full_text),
            },
        )
        return await knowledge_store.save_paper(paper)


institutional_access_service = InstitutionalAccessService()
