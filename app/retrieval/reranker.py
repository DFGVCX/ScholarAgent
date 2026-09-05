from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import aiohttp

from app.config import get_settings


class RerankUnavailable(RuntimeError):
    """The configured reranking service is disabled, unreachable, or invalid."""


class _RetryableRerankError(RuntimeError):
    pass


@dataclass(frozen=True)
class RerankResult:
    index: int
    score: float


@dataclass(frozen=True)
class RerankUsage:
    status: str
    model: str
    document_count: int
    reported_tokens: int
    duration_ms: int
    request_id: str = ""
    error_type: str | None = None


class QwenRerankerClient:
    """DashScope text reranker with support for both native Qwen payload shapes."""

    DEFAULT_MODEL = "qwen3.7-text-rerank"
    DEFAULT_ENDPOINT = (
        "https://dashscope.aliyuncs.com/api/v1/services/rerank/"
        "text-rerank/text-rerank"
    )

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = 8.0,
        max_retries: int = 1,
        max_document_chars: int = 12000,
        session_factory: Callable[..., Any] = aiohttp.ClientSession,
        sleep_func: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        if not endpoint.strip():
            raise ValueError("reranker endpoint is required")
        if not model.strip():
            raise ValueError("reranker model is required")
        self.endpoint = endpoint.strip()
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.max_retries = max(0, int(max_retries))
        self.max_document_chars = max(1000, int(max_document_chars))
        self.session_factory = session_factory
        self.sleep_func = sleep_func
        self.last_usage: RerankUsage | None = None

    @classmethod
    def from_settings(cls) -> "QwenRerankerClient":
        settings = get_settings()
        return cls(
            endpoint=settings.rag_reranker_endpoint,
            api_key=settings.rag_reranker_api_key or settings.rag_embedding_api_key,
            model=settings.rag_reranker_model,
            timeout_seconds=settings.rag_reranker_timeout_seconds,
        )

    async def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int
    ) -> list[RerankResult]:
        values = [str(value)[: self.max_document_chars] for value in documents]
        if not query.strip() or not values:
            return []
        if not self.api_key:
            raise RerankUnavailable("reranker API key is not configured")
        top_n = max(1, min(int(top_n), len(values)))
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = self._payload(query.strip(), values, top_n)
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        started = perf_counter()
        status = "failed"
        error_type: str | None = None
        request_id = ""
        reported_tokens = 0
        try:
            async with self.session_factory(timeout=timeout) as session:
                for attempt in range(self.max_retries + 1):
                    try:
                        async with session.post(
                            self.endpoint, json=payload, headers=headers
                        ) as response:
                            data = await response.json(content_type=None)
                            request_id = str(data.get("request_id") or data.get("id") or "")
                            reported_tokens = self._usage_tokens(data)
                            if response.status == 429 or response.status >= 500:
                                raise _RetryableRerankError(
                                    f"reranker returned HTTP {response.status}"
                                )
                            if response.status >= 400:
                                message = str(data.get("message") or data.get("code") or "request rejected")
                                raise RerankUnavailable(
                                    f"reranker returned HTTP {response.status}: {message[:300]}"
                                )
                            results = self._parse_results(data, len(values))
                            status = "succeeded"
                            return results
                    except (_RetryableRerankError, aiohttp.ClientError, TimeoutError, OSError) as exc:
                        if attempt >= self.max_retries:
                            raise RerankUnavailable(
                                f"reranker request failed after {attempt + 1} attempts: {exc}"
                            ) from exc
                        await self.sleep_func(0.25 * (2**attempt))
        except RerankUnavailable as exc:
            error_type = type(exc).__name__
            raise
        except asyncio.CancelledError:
            error_type = "CancelledError"
            raise
        finally:
            self.last_usage = RerankUsage(
                status=status,
                model=self.model,
                document_count=len(values),
                reported_tokens=reported_tokens,
                duration_ms=max(0, round((perf_counter() - started) * 1000)),
                request_id=request_id,
                error_type=error_type,
            )

    def _payload(self, query: str, documents: list[str], top_n: int) -> dict[str, Any]:
        instruct = "Given an academic search query, retrieve passages that provide direct evidence."
        if self.model == "qwen3-rerank" or "/compatible-api/" in self.endpoint:
            return {
                "model": self.model,
                "query": query,
                "documents": documents,
                "top_n": top_n,
                "instruct": instruct,
            }
        return {
            "model": self.model,
            "input": {"query": query, "documents": documents},
            "parameters": {"top_n": top_n, "instruct": instruct},
        }

    @staticmethod
    def _usage_tokens(payload: dict[str, Any]) -> int:
        usage = payload.get("usage") or {}
        value = usage.get("prompt_tokens", usage.get("total_tokens", 0))
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0

    @staticmethod
    def _parse_results(payload: dict[str, Any], count: int) -> list[RerankResult]:
        output = payload.get("output") if isinstance(payload.get("output"), dict) else payload
        rows = output.get("results") if isinstance(output, dict) else None
        if not isinstance(rows, list):
            raise RerankUnavailable("reranker response does not contain results")
        results: list[RerankResult] = []
        seen: set[int] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                index = int(row["index"])
                score = float(row["relevance_score"])
            except (KeyError, TypeError, ValueError):
                continue
            if index < 0 or index >= count or index in seen:
                continue
            seen.add(index)
            results.append(RerankResult(index=index, score=score))
        if not results:
            raise RerankUnavailable("reranker response contains no valid ranking rows")
        return results
