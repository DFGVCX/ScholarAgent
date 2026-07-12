from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from app.services.runtime_config import read_runtime_config


@dataclass(frozen=True)
class Settings:
    app_name: str = "ScholarAgent"
    env: str = "development"
    api_keys: str = "demo-key:tenant_demo:user_demo"
    allow_mock_data: bool = False
    storage_backend: str = "auto"
    primary_model_provider: str = "none"
    secondary_model_provider: str = "none"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_api_key: str = ""
    anthropic_model: str = ""
    external_source_provider: str = "real"
    external_source_timeout_seconds: float = 8.0
    rag_index_backend: str = "auto"
    rag_retrieval_mode: str = "hybrid"
    rag_embedding_provider: str = "lexical"
    rag_embedding_base_url: str = ""
    rag_embedding_api_key: str = ""
    rag_embedding_model: str = ""
    rag_embedding_dimensions: int = 0
    rag_chunk_size: int = 900
    rag_chunk_overlap: int = 120
    rag_chunk_strategy: str = "paragraph"
    rag_top_k: int = 8
    rag_candidate_limit: int = 800
    rag_bm25_k1: float = 1.5
    rag_bm25_b: float = 0.75
    rag_recency_half_life_days: float = 365.0
    rag_vector_weight: float = 0.35
    rag_bm25_weight: float = 0.40
    rag_recency_weight: float = 0.10
    rag_preference_weight: float = 0.15
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = "https://cloud.langfuse.com"
    langfuse_environment: str = "development"
    model_response_cache_enabled: bool = True
    model_response_cache_max_entries: int = 256
    storage_dir: Path = Path("storage/runtime")
    upload_dir: Path = Path("storage/uploads")
    cors_allow_origins: tuple[str, ...] = ("*",)


def validate_release_settings(settings: Settings) -> None:
    """Fail fast when a production process still uses development defaults."""
    if settings.env != "production":
        return
    if settings.allow_mock_data:
        raise RuntimeError("SCHOLAR_ALLOW_MOCK_DATA must be false in production")
    if not settings.api_keys or any(
        marker in settings.api_keys for marker in ("demo-key", "acme-key", "change-me")
    ):
        raise RuntimeError("Configure non-demo SCHOLAR_API_KEYS before production startup")
    if not settings.cors_allow_origins or "*" in settings.cors_allow_origins:
        raise RuntimeError("Configure explicit SCHOLAR_CORS_ALLOW_ORIGINS in production")


def _setting_value(overrides: dict[str, str], name: str, default: str = "") -> str:
    value = overrides.get(name)
    if value is not None:
        return value
    return os.getenv(name, default)


def _setting_bool(overrides: dict[str, str], name: str, default: bool = False) -> bool:
    raw = overrides.get(name, os.getenv(name))
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _setting_float(overrides: dict[str, str], name: str, default: float) -> float:
    raw = overrides.get(name, os.getenv(name))
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _setting_int(overrides: dict[str, str], name: str, default: int) -> int:
    raw = overrides.get(name, os.getenv(name))
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def get_settings() -> Settings:
    overrides = read_runtime_config()
    storage_dir = Path(_setting_value(overrides, "SCHOLAR_STORAGE_DIR", "storage/runtime"))
    upload_dir = Path(_setting_value(overrides, "SCHOLAR_UPLOAD_DIR", "storage/uploads"))
    cors_origins = tuple(
        item.strip()
        for item in _setting_value(overrides, "SCHOLAR_CORS_ALLOW_ORIGINS", "*").split(",")
        if item.strip()
    )
    return Settings(
        env=_setting_value(overrides, "SCHOLAR_ENV", "development"),
        api_keys=_setting_value(overrides, "SCHOLAR_API_KEYS", "demo-key:tenant_demo:user_demo"),
        allow_mock_data=_setting_bool(overrides, "SCHOLAR_ALLOW_MOCK_DATA", False),
        storage_backend=_setting_value(overrides, "SCHOLAR_STORAGE_BACKEND", "auto"),
        primary_model_provider=_setting_value(overrides, "SCHOLAR_PRIMARY_MODEL_PROVIDER", "none").strip().lower(),
        secondary_model_provider=_setting_value(overrides, "SCHOLAR_SECONDARY_MODEL_PROVIDER", "none").strip().lower(),
        llm_base_url=_setting_value(overrides, "SCHOLAR_LLM_BASE_URL", "").strip(),
        llm_api_key=_setting_value(overrides, "SCHOLAR_LLM_API_KEY", "").strip(),
        llm_model=_setting_value(overrides, "SCHOLAR_LLM_MODEL", "").strip(),
        anthropic_base_url=_setting_value(overrides, "SCHOLAR_ANTHROPIC_BASE_URL", "https://api.anthropic.com").strip(),
        anthropic_api_key=_setting_value(overrides, "SCHOLAR_ANTHROPIC_API_KEY", "").strip(),
        anthropic_model=_setting_value(overrides, "SCHOLAR_ANTHROPIC_MODEL", "").strip(),
        external_source_provider=_setting_value(overrides, "SCHOLAR_EXTERNAL_SOURCE_PROVIDER", "real").strip().lower(),
        external_source_timeout_seconds=_setting_float(overrides, "SCHOLAR_EXTERNAL_SOURCE_TIMEOUT_SECONDS", 8.0),
        rag_index_backend=_setting_value(overrides, "SCHOLAR_RAG_INDEX_BACKEND", "auto").strip().lower(),
        rag_retrieval_mode=_setting_value(overrides, "SCHOLAR_RAG_RETRIEVAL_MODE", "hybrid").strip().lower(),
        rag_embedding_provider=_setting_value(overrides, "SCHOLAR_RAG_EMBEDDING_PROVIDER", "lexical").strip().lower(),
        rag_embedding_base_url=_setting_value(overrides, "SCHOLAR_RAG_EMBEDDING_BASE_URL", "").strip(),
        rag_embedding_api_key=_setting_value(overrides, "SCHOLAR_RAG_EMBEDDING_API_KEY", "").strip(),
        rag_embedding_model=_setting_value(overrides, "SCHOLAR_RAG_EMBEDDING_MODEL", "").strip(),
        rag_embedding_dimensions=max(0, _setting_int(overrides, "SCHOLAR_RAG_EMBEDDING_DIMENSIONS", 0)),
        rag_chunk_size=max(200, _setting_int(overrides, "SCHOLAR_RAG_CHUNK_SIZE", 900)),
        rag_chunk_overlap=max(0, _setting_int(overrides, "SCHOLAR_RAG_CHUNK_OVERLAP", 120)),
        rag_chunk_strategy=_setting_value(overrides, "SCHOLAR_RAG_CHUNK_STRATEGY", "paragraph").strip().lower(),
        rag_top_k=max(1, _setting_int(overrides, "SCHOLAR_RAG_TOP_K", 8)),
        rag_candidate_limit=max(20, _setting_int(overrides, "SCHOLAR_RAG_CANDIDATE_LIMIT", 800)),
        rag_bm25_k1=max(0.1, _setting_float(overrides, "SCHOLAR_RAG_BM25_K1", 1.5)),
        rag_bm25_b=min(1.0, max(0.0, _setting_float(overrides, "SCHOLAR_RAG_BM25_B", 0.75))),
        rag_recency_half_life_days=max(1.0, _setting_float(overrides, "SCHOLAR_RAG_RECENCY_HALF_LIFE_DAYS", 365.0)),
        rag_vector_weight=max(0.0, _setting_float(overrides, "SCHOLAR_RAG_VECTOR_WEIGHT", 0.35)),
        rag_bm25_weight=max(0.0, _setting_float(overrides, "SCHOLAR_RAG_BM25_WEIGHT", 0.40)),
        rag_recency_weight=max(0.0, _setting_float(overrides, "SCHOLAR_RAG_RECENCY_WEIGHT", 0.10)),
        rag_preference_weight=max(0.0, _setting_float(overrides, "SCHOLAR_RAG_PREFERENCE_WEIGHT", 0.15)),
        langfuse_enabled=_setting_bool(overrides, "SCHOLAR_LANGFUSE_ENABLED", False),
        langfuse_public_key=_setting_value(overrides, "SCHOLAR_LANGFUSE_PUBLIC_KEY", "").strip(),
        langfuse_secret_key=_setting_value(overrides, "SCHOLAR_LANGFUSE_SECRET_KEY", "").strip(),
        langfuse_base_url=_setting_value(overrides, "SCHOLAR_LANGFUSE_BASE_URL", "https://cloud.langfuse.com").strip(),
        langfuse_environment=_setting_value(overrides, "SCHOLAR_LANGFUSE_ENVIRONMENT", _setting_value(overrides, "SCHOLAR_ENV", "development")).strip(),
        model_response_cache_enabled=_setting_bool(overrides, "SCHOLAR_MODEL_CACHE_ENABLED", True),
        model_response_cache_max_entries=max(16, _setting_int(overrides, "SCHOLAR_MODEL_CACHE_MAX_ENTRIES", 256)),
        storage_dir=storage_dir,
        upload_dir=upload_dir,
        cors_allow_origins=cors_origins or ("*",),
    )
