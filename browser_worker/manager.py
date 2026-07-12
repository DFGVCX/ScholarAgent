from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from playwright.async_api import Playwright, async_playwright

from browser_worker.cnki_adapter import download_cnki_result, search_cnki
from browser_worker.models import BrowserSession


def _safe_segment(value: str) -> str:
    if not re.search(r"[A-Za-z0-9]", value):
        raise ValueError("invalid session identity")
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    if not cleaned:
        raise ValueError("invalid session identity")
    return cleaned[:120]


class BrowserSessionManager:
    def __init__(self) -> None:
        self.playwright: Playwright | None = None
        self.sessions: dict[str, BrowserSession] = {}
        self.storage_root = Path(os.getenv("SCHOLAR_STORAGE_DIR", "storage/runtime")).resolve()
        self.idle_timeout_seconds = max(300, int(os.getenv("SCHOLAR_BROWSER_IDLE_TIMEOUT_SECONDS", "3600")))
        self._cleanup_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self.playwright is None:
            self.playwright = await async_playwright().start()
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        for session_id in list(self.sessions):
            await self.close_session(session_id, preserve_manifest=True)
        if self.playwright is not None:
            await self.playwright.stop()
            self.playwright = None

    def _manifest_path(self, session_id: str) -> Path:
        return self.storage_root / "browser-sessions" / "manifests" / f"{_safe_segment(session_id)}.json"

    def _persist(self, session: BrowserSession) -> None:
        path = self._manifest_path(session.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": session.session_id,
            "tenant_id": session.tenant_id,
            "user_id": session.user_id,
            "login_url": session.login_url,
            "status": session.status,
            "profile_dir": str(session.profile_dir),
            "download_dir": str(session.download_dir),
            "search_results": session.search_results,
            "created_at": session.created_at,
            "last_activity_at": session.last_activity_at,
            "last_error": session.last_error,
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    async def _launch_context(self, profile_dir: Path, download_dir: Path, headless: bool):
        assert self.playwright is not None
        channel = os.getenv("SCHOLAR_BROWSER_CHANNEL", "msedge").strip() or None
        options = {
            "headless": headless,
            "accept_downloads": True,
            "downloads_path": str(download_dir),
            "viewport": {"width": 1440, "height": 900},
            "args": ["--no-first-run", "--disable-features=AutomationControlled"],
        }
        if channel:
            options["channel"] = channel
        try:
            return await self.playwright.chromium.launch_persistent_context(str(profile_dir), **options)
        except Exception:
            if not channel:
                raise
            options.pop("channel", None)
            return await self.playwright.chromium.launch_persistent_context(str(profile_dir), **options)

    async def _restore(self, session_id: str) -> BrowserSession:
        path = self._manifest_path(session_id)
        if not path.exists():
            raise KeyError("browser session not found")
        payload = json.loads(path.read_text(encoding="utf-8"))
        await self.start()
        profile_dir = Path(payload["profile_dir"])
        download_dir = Path(payload["download_dir"])
        profile_dir.mkdir(parents=True, exist_ok=True)
        download_dir.mkdir(parents=True, exist_ok=True)
        context = await self._launch_context(profile_dir, download_dir, headless=True)
        page = context.pages[-1] if context.pages else await context.new_page()
        if not page.url or page.url == "about:blank":
            await page.goto(payload["login_url"], wait_until="domcontentloaded", timeout=45_000)
        session = BrowserSession(
            session_id=payload["session_id"], tenant_id=payload["tenant_id"],
            user_id=payload["user_id"], login_url=payload["login_url"],
            context=context, page=page, profile_dir=profile_dir, download_dir=download_dir,
            status="authenticated" if payload.get("status") == "authenticated" else "awaiting_user_login",
            search_results=list(payload.get("search_results") or []),
            created_at=float(payload.get("created_at") or time.time()),
            last_activity_at=time.time(), last_error="",
        )
        self.sessions[session_id] = session
        self._persist(session)
        return session

    async def _ensure(self, session_id: str) -> BrowserSession:
        return self.sessions.get(session_id) or await self._restore(session_id)

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            cutoff = time.time() - self.idle_timeout_seconds
            for session_id, session in list(self.sessions.items()):
                if session.last_activity_at < cutoff and not session.operation_lock.locked():
                    await self.close_session(session_id, preserve_manifest=True)

    async def create_session(
        self,
        *,
        session_id: str,
        tenant_id: str,
        user_id: str,
        login_url: str,
        headless: bool = False,
    ) -> dict[str, Any]:
        await self.start()
        if session_id in self.sessions:
            current = await self.status(session_id)
            if current.get("status") != "closed":
                return current
            stale = self.sessions.pop(session_id)
            try:
                await stale.context.close()
            except Exception:
                pass
        identity = [_safe_segment(value) for value in (tenant_id, user_id, session_id)]
        profile_dir = self.storage_root / "browser-sessions" / identity[0] / identity[1] / identity[2]
        download_dir = self.storage_root / "browser-downloads" / identity[0] / identity[1] / identity[2]
        profile_dir.mkdir(parents=True, exist_ok=True)
        download_dir.mkdir(parents=True, exist_ok=True)
        context = await self._launch_context(profile_dir, download_dir, headless)
        page = context.pages[0] if context.pages else await context.new_page()
        session = BrowserSession(
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            login_url=login_url,
            context=context,
            page=page,
            profile_dir=profile_dir,
            download_dir=download_dir,
        )
        self.sessions[session_id] = session
        await page.goto(login_url, wait_until="domcontentloaded", timeout=45_000)
        self._persist(session)
        return await self.status(session_id)

    def _get(self, session_id: str) -> BrowserSession:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError("browser session not found or worker was restarted")
        return session

    async def status(self, session_id: str) -> dict[str, Any]:
        session = await self._ensure(session_id)
        session.last_activity_at = time.time()
        pages = [page for page in session.context.pages if not page.is_closed()]
        if not pages:
            session.status = "closed"
            return {
                "session_id": session.session_id,
                "tenant_id": session.tenant_id,
                "user_id": session.user_id,
                "status": "closed",
                "current_url": "",
                "title": "",
                "page_count": 0,
                "search_count": len(session.search_results),
                "recoverable": self._manifest_path(session_id).exists(),
                "last_error": session.last_error,
            }
        current = pages[-1]
        session.page = current
        result = {
            "session_id": session.session_id,
            "tenant_id": session.tenant_id,
            "user_id": session.user_id,
            "status": session.status,
            "current_url": current.url,
            "title": await current.title(),
            "page_count": len(pages),
            "search_count": len(session.search_results),
            "recoverable": True,
            "last_activity_at": session.last_activity_at,
            "last_error": session.last_error,
        }
        self._persist(session)
        return result

    async def mark_authenticated(self, session_id: str) -> dict[str, Any]:
        session = await self._ensure(session_id)
        session.status = "authenticated"
        session.last_error = ""
        self._persist(session)
        return await self.status(session_id)

    async def search_cnki(self, session_id: str, query: str, limit: int) -> dict[str, Any]:
        session = await self._ensure(session_id)
        pages = [page for page in session.context.pages if not page.is_closed()]
        if not pages:
            session.status = "closed"
            raise RuntimeError("机构浏览器已经关闭，请重新连接并完成登录")
        # Institution login flows can finish in a newly opened tab.
        session.page = pages[-1]
        async with session.operation_lock:
            session.status = "searching"
            try:
                for attempt in range(2):
                    try:
                        session.search_results = await search_cnki(session.page, query, limit)
                        break
                    except Exception:
                        if attempt:
                            raise
                        await asyncio.sleep(0.8)
                session.status = "authenticated"
                session.last_error = ""
                session.last_activity_at = time.time()
                self._persist(session)
                return {"items": session.search_results, **await self.status(session_id)}
            except Exception as exc:
                session.status = "error"
                session.last_error = str(exc)
                self._persist(session)
                raise

    async def download_cnki(self, session_id: str, indexes: list[int]) -> dict[str, Any]:
        session = await self._ensure(session_id)
        if not [page for page in session.context.pages if not page.is_closed()]:
            session.status = "closed"
            raise RuntimeError("机构浏览器已经关闭，请重新连接并完成登录")
        selected = []
        for index in indexes:
            offset = int(index) - 1
            if offset < 0 or offset >= len(session.search_results):
                raise ValueError(f"invalid search result index: {index}")
            selected.append(session.search_results[offset])
        async with session.operation_lock:
            session.status = "downloading"
            results = []
            try:
                for item in selected:
                    results.append(await download_cnki_result(session.context, item, session.download_dir))
                session.status = "authenticated"
                session.last_error = ""
                session.last_activity_at = time.time()
                self._persist(session)
                return {"items": results, **await self.status(session_id)}
            except Exception as exc:
                session.status = "error"
                session.last_error = str(exc)
                self._persist(session)
                raise

    async def close_session(self, session_id: str, preserve_manifest: bool = False) -> None:
        session = self.sessions.pop(session_id, None)
        if session is not None:
            await session.context.close()
        if not preserve_manifest:
            self._manifest_path(session_id).unlink(missing_ok=True)

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.playwright is not None else "starting",
            "sessions": len(self.sessions),
            "busy_sessions": sum(1 for item in self.sessions.values() if item.operation_lock.locked()),
            "recoverable_sessions": len(list((self.storage_root / "browser-sessions" / "manifests").glob("*.json")))
            if (self.storage_root / "browser-sessions" / "manifests").exists() else 0,
        }


browser_session_manager = BrowserSessionManager()
