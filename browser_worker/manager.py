from __future__ import annotations

import os
import re
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

    async def start(self) -> None:
        if self.playwright is None:
            self.playwright = await async_playwright().start()

    async def stop(self) -> None:
        for session_id in list(self.sessions):
            await self.close_session(session_id)
        if self.playwright is not None:
            await self.playwright.stop()
            self.playwright = None

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
        assert self.playwright is not None
        context = await self.playwright.chromium.launch_persistent_context(
            str(profile_dir),
            channel="msedge",
            headless=headless,
            accept_downloads=True,
            downloads_path=str(download_dir),
            viewport={"width": 1440, "height": 900},
            args=["--no-first-run", "--disable-features=AutomationControlled"],
        )
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
        return await self.status(session_id)

    def _get(self, session_id: str) -> BrowserSession:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError("browser session not found or worker was restarted")
        return session

    async def status(self, session_id: str) -> dict[str, Any]:
        session = self._get(session_id)
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
            }
        current = pages[-1]
        session.page = current
        return {
            "session_id": session.session_id,
            "tenant_id": session.tenant_id,
            "user_id": session.user_id,
            "status": session.status,
            "current_url": current.url,
            "title": await current.title(),
            "page_count": len(pages),
            "search_count": len(session.search_results),
        }

    async def mark_authenticated(self, session_id: str) -> dict[str, Any]:
        session = self._get(session_id)
        session.status = "authenticated"
        return await self.status(session_id)

    async def search_cnki(self, session_id: str, query: str, limit: int) -> dict[str, Any]:
        session = self._get(session_id)
        if not [page for page in session.context.pages if not page.is_closed()]:
            session.status = "closed"
            raise RuntimeError("机构浏览器已经关闭，请重新连接并完成登录")
        session.status = "searching"
        try:
            session.search_results = await search_cnki(session.page, query, limit)
            session.status = "authenticated"
            return {"items": session.search_results, **await self.status(session_id)}
        except Exception:
            session.status = "error"
            raise

    async def download_cnki(self, session_id: str, indexes: list[int]) -> dict[str, Any]:
        session = self._get(session_id)
        if not [page for page in session.context.pages if not page.is_closed()]:
            session.status = "closed"
            raise RuntimeError("机构浏览器已经关闭，请重新连接并完成登录")
        selected = []
        for index in indexes:
            offset = int(index) - 1
            if offset < 0 or offset >= len(session.search_results):
                raise ValueError(f"invalid search result index: {index}")
            selected.append(session.search_results[offset])
        session.status = "downloading"
        results = []
        try:
            for item in selected:
                results.append(await download_cnki_result(session.context, item, session.download_dir))
            session.status = "authenticated"
            return {"items": results, **await self.status(session_id)}
        except Exception:
            session.status = "error"
            raise

    async def close_session(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session is not None:
            await session.context.close()


browser_session_manager = BrowserSessionManager()
