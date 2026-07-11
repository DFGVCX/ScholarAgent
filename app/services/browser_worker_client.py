from __future__ import annotations

import os
from typing import Any

import httpx


class BrowserWorkerError(RuntimeError):
    pass


class BrowserWorkerClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("SCHOLAR_BROWSER_WORKER_URL", "http://127.0.0.1:8002").rstrip("/")
        self.token = os.getenv("SCHOLAR_BROWSER_WORKER_TOKEN", "").strip()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            timeout = httpx.Timeout(120.0, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout, headers=self._headers()) as client:
                response = await client.request(method, f"{self.base_url}{path}", json=payload)
        except httpx.TimeoutException as exc:
            raise BrowserWorkerError("Browser Worker 下载超时，未获得可校验的 PDF/CAJ 文件") from exc
        except Exception as exc:
            message = str(exc).strip() or exc.__class__.__name__
            raise BrowserWorkerError(f"Browser Worker 不可用：{message}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = response.text
            raise BrowserWorkerError(str(detail or f"HTTP {response.status_code}"))
        return response.json()

    async def start_session(
        self,
        *,
        session_id: str,
        tenant_id: str,
        user_id: str,
        login_url: str,
        headless: bool = False,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/sessions",
            {
                "session_id": session_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "login_url": login_url,
                "headless": headless,
            },
        )

    async def status(self, session_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/sessions/{session_id}")

    async def mark_authenticated(self, session_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/sessions/{session_id}/authenticated", {})

    async def search_cnki(self, session_id: str, query: str, limit: int = 20) -> dict[str, Any]:
        return await self._request(
            "POST", f"/sessions/{session_id}/cnki/search", {"query": query, "limit": limit}
        )

    async def download_cnki(self, session_id: str, indexes: list[int]) -> dict[str, Any]:
        return await self._request(
            "POST", f"/sessions/{session_id}/cnki/download", {"indexes": indexes}
        )

    async def close(self, session_id: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/sessions/{session_id}")


browser_worker_client = BrowserWorkerClient()
