from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.services import mysql_store
from app.services.chroma_store import chroma_store
from app.services.rag_service import rag_service
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
    sqlite_available = mysql_store.is_available()
    sqlite_table_count = 0
    if sqlite_available:
        try:
            row = mysql_store.fetch_one(
                "SELECT COUNT(*) AS table_count FROM sqlite_master WHERE type='table' AND name LIKE 'scholar_%'"
            )
            sqlite_table_count = int((row or {}).get("table_count") or 0)
        except Exception:
            sqlite_table_count = 0
    # ChromaDB stats
    chroma_stats: dict[str, object] = {"available": False, "chunk_count": 0, "paper_count": 0}
    try:
        stats = chroma_store._collection.count()
        chroma_stats["available"] = True
        chroma_stats["chunk_count"] = stats
    except Exception:
        pass
    return {
        "status": "ok",
        "sqlite": {
            "database": str(mysql_store._db_path()),
            "available": sqlite_available,
            "table_count": sqlite_table_count,
        },
        "chromadb": chroma_stats,
        "storage_backend": settings.storage_backend,
        "runtime_backend": {
            "storage": "sqlite" if sqlite_available else "json",
            "rag": rag_service.backend(),
            "tracing": trace_recorder.status(),
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
