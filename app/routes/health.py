from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.services import mysql_store
from app.services.tracing import trace_recorder
from skills.survey_generation.tools.formatter import CitationFormatter

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
    database_available = mysql_store.is_available()
    database_info: dict[str, object] = {
        "engine": "postgresql",
        "database": mysql_store.configured_database_name(),
        "available": database_available,
        "pgvector": False,
        "table_count": 0,
    }
    if database_available:
        try:
            row = mysql_store.fetch_one(
                "SELECT current_database() AS database, current_setting('server_version') AS version, "
                "EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector') AS pgvector, "
                "(SELECT COUNT(*) FROM pg_tables WHERE schemaname = 'public') AS table_count"
            )
            if row:
                database_info.update(row)
                database_info["table_count"] = int(row.get("table_count") or 0)
        except Exception:
            database_info["available"] = False
    return {
        "status": "ok" if database_info["available"] else "degraded",
        "database": database_info,
        "storage_backend": "postgresql",
        "runtime_backend": {
            "storage": "postgresql",
            "rag": "pgvector",
            "tracing": trace_recorder.status(),
        },
        "rag": {
            "index_backend": "pgvector",
            "runtime_backend": "pgvector",
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
