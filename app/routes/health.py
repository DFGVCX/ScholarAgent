from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.services import mysql_store
from app.services.rag_service import rag_service
from skills.survey_generation.tools.formatter import CitationFormatter

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None

router = APIRouter(prefix="/health", tags=["health"])

ANTHROPIC_PROVIDERS = {"anthropic", "claude"}
LOCAL_MODEL_PROVIDERS = {"ollama", "vllm", "lmstudio"}


@router.get("")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "scholar-agent"}


@router.get("/infra")
async def infra_health() -> dict[str, object]:
    settings = get_settings()
    formatter = CitationFormatter()
    formatter.load()
    redis_status: dict[str, object] = {"url": settings.redis_url, "available": False}
    mysql_available = mysql_store.is_available()
    mysql_table_count = 0
    if mysql_available:
        try:
            row = mysql_store.fetch_one(
                """
                SELECT COUNT(*) AS table_count
                FROM information_schema.tables
                WHERE table_schema = %s AND table_name LIKE 'scholar_%%'
                """,
                (mysql_store.configured_database_name(),),
            )
            mysql_table_count = int((row or {}).get("table_count") or 0)
        except Exception:
            mysql_table_count = 0
    if redis is not None:
        try:
            client = redis.Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=0.3,
                socket_timeout=0.3,
                decode_responses=True,
            )
            redis_status["available"] = bool(client.ping())
        except Exception as exc:
            redis_status["error"] = str(exc)
    return {
        "status": "ok",
        "mysql": {
            "database": mysql_store.configured_database_name(),
            "available": mysql_available,
            "table_count": mysql_table_count,
        },
        "redis": redis_status,
        "storage_backend": settings.storage_backend,
        "runtime_backend": {
            "storage": "mysql" if mysql_available else "json",
            "rag": rag_service.backend(),
        },
        "rag": {
            "index_backend": settings.rag_index_backend,
            "runtime_backend": rag_service.backend(),
            "retrieval_mode": settings.rag_retrieval_mode,
            "embedding_provider": settings.rag_embedding_provider,
            "embedding_model": settings.rag_embedding_model,
            "chunk_size": settings.rag_chunk_size,
            "chunk_overlap": settings.rag_chunk_overlap,
            "top_k": settings.rag_top_k,
            "candidate_limit": settings.rag_candidate_limit,
        },
        "mock_data": {
            "allowed": settings.allow_mock_data,
            "external_source_provider": settings.external_source_provider,
            "rag_embedding_provider": settings.rag_embedding_provider,
        },
        "model": {
            "primary_provider": settings.primary_model_provider,
            "secondary_provider": settings.secondary_model_provider,
            "configured": (
                (
                    settings.primary_model_provider in ANTHROPIC_PROVIDERS
                    and bool(settings.anthropic_api_key and settings.anthropic_model)
                )
                or (
                    settings.primary_model_provider in LOCAL_MODEL_PROVIDERS
                    and bool(settings.llm_model)
                )
                or (
                    settings.primary_model_provider not in {"", "none", *ANTHROPIC_PROVIDERS, *LOCAL_MODEL_PROVIDERS}
                    and bool(settings.llm_api_key and settings.llm_model)
                )
                or (settings.primary_model_provider in {"deterministic", "mock"} and settings.allow_mock_data)
            ),
            "base_url": settings.anthropic_base_url
            if settings.primary_model_provider in ANTHROPIC_PROVIDERS
            else settings.llm_base_url,
            "model": settings.anthropic_model
            if settings.primary_model_provider in ANTHROPIC_PROVIDERS
            else settings.llm_model,
        },
        "external_sources": {
            "provider": settings.external_source_provider,
            "timeout_seconds": settings.external_source_timeout_seconds,
        },
        "citeadapt": formatter.status(),
    }
