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
    rag_top_k: int = 8
    rag_candidate_limit: int = 800
    storage_dir: Path = Path("storage/runtime")
    upload_dir: Path = Path("storage/uploads")
    cors_allow_origins: tuple[str, ...] = ("*",)


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
        rag_top_k=max(1, _setting_int(overrides, "SCHOLAR_RAG_TOP_K", 8)),
        rag_candidate_limit=max(20, _setting_int(overrides, "SCHOLAR_RAG_CANDIDATE_LIMIT", 800)),
        storage_dir=storage_dir,
        upload_dir=upload_dir,
    )
