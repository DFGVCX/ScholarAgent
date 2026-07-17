from __future__ import annotations

from collections.abc import Callable, Sequence
import math
from typing import Any

import aiohttp

from app.config import get_settings


class EmbeddingUnavailable(RuntimeError):
    """The configured embedding service could not be reached or rejected the request."""


class EmbeddingResponseError(EmbeddingUnavailable):
    """The embedding service returned vectors that violate the storage contract."""


class QwenEmbeddingClient:
    MODEL = "Qwen3-Embedding-0.6B"
    DIMENSIONS = 1024

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        model: str = MODEL,
        dimensions: int = DIMENSIONS,
        timeout_seconds: float = 30.0,
        session_factory: Callable[..., Any] = aiohttp.ClientSession,
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
        values = [str(value).strip() for value in texts]
        if not values:
            return []
        if any(not value for value in values):
            raise ValueError("embedding input cannot be empty")

        payload = {
            "model": self.model,
            "input": values,
            "dimensions": self.dimensions,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with self.session_factory(timeout=timeout) as session:
                async with session.post(
                    f"{self.base_url}/v1/embeddings", json=payload, headers=headers
                ) as response:
                    data = await response.json()
                    if response.status >= 400:
                        raise EmbeddingUnavailable(
                            f"Qwen embedding returned HTTP {response.status}: {data}"
                        )
        except EmbeddingUnavailable:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            raise EmbeddingUnavailable(f"Qwen embedding request failed: {exc}") from exc

        return self._validate_and_normalize(data, expected_count=len(values))

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
