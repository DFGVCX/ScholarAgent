from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import math
from time import perf_counter
from typing import Any

import aiohttp

from app.config import get_settings


class EmbeddingUnavailable(RuntimeError):
    """The configured embedding service could not be reached or rejected the request."""


class EmbeddingResponseError(EmbeddingUnavailable):
    """The embedding service returned vectors that violate the storage contract."""


class _RetryableEmbeddingError(RuntimeError):
    pass


@dataclass(frozen=True)
class EmbeddingUsage:
    """Provider-reported usage for one logical embed() call.

    `reported_tokens` is never guessed from characters. It is the sum of
    provider `usage.prompt_tokens` (or `total_tokens` when prompt_tokens is
    absent) for successful HTTP responses.
    """

    status: str
    model: str
    input_count: int
    request_count: int
    successful_request_count: int
    failed_request_count: int
    cancelled_request_count: int
    reported_tokens: int
    usage_reported_requests: int
    successful_usage_reported_requests: int
    duration_ms: int
    error_type: str | None = None


class QwenEmbeddingClient:
    MODEL = "qwen3.7-text-embedding"
    DIMENSIONS = 1024
    MAX_BATCH_SIZE = 20

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        model: str = MODEL,
        dimensions: int = DIMENSIONS,
        timeout_seconds: float = 30.0,
        session_factory: Callable[..., Any] = aiohttp.ClientSession,
        max_retries: int = 3,
        retry_base_seconds: float = 0.5,
        sleep_func: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        if not base_url.strip():
            raise ValueError("Qwen embedding base_url is required")
        if not model.strip():
            raise ValueError("Qwen embedding model is required")
        if dimensions != self.DIMENSIONS:
            raise ValueError(f"embedding dimensions must be {self.DIMENSIONS}")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds
        self.session_factory = session_factory
        self.max_retries = max(0, int(max_retries))
        self.retry_base_seconds = max(0.0, float(retry_base_seconds))
        self.sleep_func = sleep_func
        self.last_usage: EmbeddingUsage | None = None

    @classmethod
    def from_settings(cls) -> "QwenEmbeddingClient":
        settings = get_settings()
        return cls(
            base_url=settings.rag_embedding_base_url,
            api_key=settings.rag_embedding_api_key,
            model=settings.rag_embedding_model,
            dimensions=settings.rag_embedding_dimensions,
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.last_usage = None
        values = [str(value).strip() for value in texts]
        if not values:
            return []
        if any(not value for value in values):
            raise ValueError("embedding input cannot be empty")

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        vectors: list[list[float]] = []
        request_count = 0
        successful_request_count = 0
        failed_request_count = 0
        cancelled_request_count = 0
        reported_tokens = 0
        usage_reported_requests = 0
        successful_usage_reported_requests = 0
        status = "failed"
        error_type: str | None = None
        started = perf_counter()
        try:
            async with self.session_factory(timeout=timeout) as session:
                for start in range(0, len(values), self.MAX_BATCH_SIZE):
                    batch = values[start : start + self.MAX_BATCH_SIZE]
                    payload = {
                        "model": self.model,
                        "input": batch,
                        "dimensions": self.dimensions,
                    }
                    for attempt in range(self.max_retries + 1):
                        try:
                            request_count += 1
                            async with session.post(
                                f"{self.base_url}/v1/embeddings", json=payload, headers=headers
                            ) as response:
                                data = await response.json()
                                usage_tokens = self._provider_tokens(data)
                                usage_reported = usage_tokens is not None
                                if usage_reported:
                                    reported_tokens += int(usage_tokens)
                                    usage_reported_requests += 1
                                if response.status == 429 or response.status >= 500:
                                    raise _RetryableEmbeddingError(
                                        f"Qwen embedding returned HTTP {response.status}: {data}"
                                    )
                                if response.status >= 400:
                                    failed_request_count += 1
                                    raise EmbeddingUnavailable(
                                        f"Qwen embedding returned HTTP {response.status}: {data}"
                                    )
                            try:
                                vectors.extend(
                                    self._validate_and_normalize(data, expected_count=len(batch))
                                )
                            except EmbeddingResponseError:
                                failed_request_count += 1
                                raise
                            successful_request_count += 1
                            if usage_reported:
                                successful_usage_reported_requests += 1
                            break
                        except (_RetryableEmbeddingError, aiohttp.ClientError, TimeoutError, OSError) as exc:
                            failed_request_count += 1
                            if attempt >= self.max_retries:
                                error_type = "EmbeddingUnavailable"
                                raise EmbeddingUnavailable(
                                    f"Qwen embedding request failed after {attempt + 1} attempts: {exc}"
                                ) from exc
                            await self.sleep_func(self.retry_base_seconds * (2**attempt))
            status = "succeeded"
        except EmbeddingUnavailable as exc:
            error_type = error_type or type(exc).__name__
            raise
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            error_type = "EmbeddingUnavailable"
            raise EmbeddingUnavailable(f"Qwen embedding request failed: {exc}") from exc
        except asyncio.CancelledError:
            status = "cancelled"
            error_type = "CancelledError"
            raise
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            unresolved_requests = max(
                0,
                request_count - successful_request_count - failed_request_count,
            )
            failed_request_count += unresolved_requests
            if status == "cancelled":
                cancelled_request_count += unresolved_requests
            self.last_usage = EmbeddingUsage(
                status=status,
                model=self.model,
                input_count=len(values),
                request_count=request_count,
                successful_request_count=successful_request_count,
                failed_request_count=failed_request_count,
                cancelled_request_count=cancelled_request_count,
                reported_tokens=reported_tokens,
                usage_reported_requests=usage_reported_requests,
                successful_usage_reported_requests=successful_usage_reported_requests,
                duration_ms=max(0, round((perf_counter() - started) * 1000)),
                error_type=error_type,
            )

        return vectors

    @staticmethod
    def _provider_tokens(payload: dict[str, Any]) -> int | None:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return None
        for key in ("prompt_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
        return None

    def _validate_and_normalize(
        self, payload: dict[str, Any], *, expected_count: int
    ) -> list[list[float]]:
        rows = payload.get("data")
        if not isinstance(rows, list) or len(rows) != expected_count:
            raise EmbeddingResponseError(
                f"expected {expected_count} embedding rows, received {len(rows) if isinstance(rows, list) else 0}"
            )
        ordered = sorted(rows, key=lambda row: int(row.get("index", 0)))
        if [int(row.get("index", -1)) for row in ordered] != list(range(expected_count)):
            raise EmbeddingResponseError("Qwen embedding response indexes are incomplete or duplicated")
        normalized: list[list[float]] = []
        for row in ordered:
            raw = row.get("embedding")
            if not isinstance(raw, list) or len(raw) != self.dimensions:
                raise EmbeddingResponseError(
                    f"Qwen embedding must contain exactly {self.dimensions} dimensions"
                )
            try:
                vector = [float(value) for value in raw]
            except (TypeError, ValueError) as exc:
                raise EmbeddingResponseError("Qwen embedding contains a non-numeric value") from exc
            if not all(math.isfinite(value) for value in vector):
                raise EmbeddingResponseError("Qwen embedding contains a non-finite value")
            norm = math.sqrt(math.fsum(value * value for value in vector))
            if norm <= 0:
                raise EmbeddingResponseError("Qwen embedding cannot be a zero vector")
            normalized.append([value / norm for value in vector])
        return normalized
