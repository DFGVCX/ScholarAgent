from __future__ import annotations

import os
from typing import Any


_RUNTIME_CONFIG_CACHE: dict[str, str] | None = None


CONFIG_KEYS: tuple[str, ...] = (
    "SCHOLAR_STORAGE_BACKEND",
    "SCHOLAR_ALLOW_MOCK_DATA",
    "SCHOLAR_EXTERNAL_SOURCE_PROVIDER",
    "SCHOLAR_EXTERNAL_SOURCE_TIMEOUT_SECONDS",
    "SCHOLAR_RAG_INDEX_BACKEND",
    "SCHOLAR_RAG_RETRIEVAL_MODE",
    "SCHOLAR_RAG_EMBEDDING_PROVIDER",
    "SCHOLAR_RAG_EMBEDDING_BASE_URL",
    "SCHOLAR_RAG_EMBEDDING_API_KEY",
    "SCHOLAR_RAG_EMBEDDING_MODEL",
    "SCHOLAR_RAG_EMBEDDING_DIMENSIONS",
    "SCHOLAR_RAG_CHUNK_SIZE",
    "SCHOLAR_RAG_CHUNK_OVERLAP",
    "SCHOLAR_RAG_CHUNK_STRATEGY",
    "SCHOLAR_RAG_TOP_K",
    "SCHOLAR_RAG_CANDIDATE_LIMIT",
    "SCHOLAR_PRIMARY_MODEL_PROVIDER",
    "SCHOLAR_SECONDARY_MODEL_PROVIDER",
    "SCHOLAR_LLM_BASE_URL",
    "SCHOLAR_LLM_API_KEY",
    "SCHOLAR_LLM_MODEL",
    "SCHOLAR_ANTHROPIC_BASE_URL",
    "SCHOLAR_ANTHROPIC_API_KEY",
    "SCHOLAR_ANTHROPIC_MODEL",
)

SECRET_KEYS: frozenset[str] = frozenset(
    {
        "SCHOLAR_LLM_API_KEY",
        "SCHOLAR_ANTHROPIC_API_KEY",
        "SCHOLAR_RAG_EMBEDDING_API_KEY",
    }
)

SELECT_OPTIONS: dict[str, tuple[str, ...]] = {
    "SCHOLAR_STORAGE_BACKEND": ("postgresql",),
    "SCHOLAR_ALLOW_MOCK_DATA": ("false", "true"),
    "SCHOLAR_EXTERNAL_SOURCE_PROVIDER": ("real", "mock"),
    "SCHOLAR_RAG_INDEX_BACKEND": ("pgvector",),
    "SCHOLAR_RAG_RETRIEVAL_MODE": ("hybrid_rrf", "lexical"),
    "SCHOLAR_RAG_EMBEDDING_PROVIDER": ("qwen",),
    "SCHOLAR_RAG_CHUNK_STRATEGY": ("paragraph", "fixed"),
    "SCHOLAR_PRIMARY_MODEL_PROVIDER": (
        "none",
        "openai-compatible",
        "openai",
        "azure-openai",
        "deepseek",
        "qwen",
        "dashscope",
        "moonshot",
        "zhipu",
        "baichuan",
        "minimax",
        "stepfun",
        "yi",
        "doubao",
        "volcengine",
        "siliconflow",
        "openrouter",
        "groq",
        "together",
        "fireworks",
        "mistral",
        "perplexity",
        "ollama",
        "vllm",
        "lmstudio",
        "anthropic",
        "claude",
        "gemini",
        "cohere",
        "deterministic",
        "mock",
    ),
    "SCHOLAR_SECONDARY_MODEL_PROVIDER": (
        "none",
        "openai-compatible",
        "openai",
        "azure-openai",
        "deepseek",
        "qwen",
        "dashscope",
        "moonshot",
        "zhipu",
        "baichuan",
        "minimax",
        "stepfun",
        "yi",
        "doubao",
        "volcengine",
        "siliconflow",
        "openrouter",
        "groq",
        "together",
        "fireworks",
        "mistral",
        "perplexity",
        "ollama",
        "vllm",
        "lmstudio",
        "anthropic",
        "claude",
        "gemini",
        "cohere",
        "deterministic",
        "mock",
    ),
}

DEFAULT_VALUES: dict[str, str] = {
    "SCHOLAR_STORAGE_BACKEND": "postgresql",
    "SCHOLAR_ALLOW_MOCK_DATA": "false",
    "SCHOLAR_EXTERNAL_SOURCE_PROVIDER": "real",
    "SCHOLAR_EXTERNAL_SOURCE_TIMEOUT_SECONDS": "8.0",
    "SCHOLAR_RAG_INDEX_BACKEND": "pgvector",
    "SCHOLAR_RAG_RETRIEVAL_MODE": "hybrid_rrf",
    "SCHOLAR_RAG_EMBEDDING_PROVIDER": "qwen",
    "SCHOLAR_RAG_EMBEDDING_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode",
    "SCHOLAR_RAG_EMBEDDING_MODEL": "Qwen3-Embedding-0.6B",
    "SCHOLAR_RAG_EMBEDDING_DIMENSIONS": "1024",
    "SCHOLAR_RAG_CHUNK_SIZE": "900",
    "SCHOLAR_RAG_CHUNK_OVERLAP": "120",
    "SCHOLAR_RAG_CHUNK_STRATEGY": "paragraph",
    "SCHOLAR_RAG_TOP_K": "8",
    "SCHOLAR_RAG_CANDIDATE_LIMIT": "800",
    "SCHOLAR_PRIMARY_MODEL_PROVIDER": "none",
    "SCHOLAR_SECONDARY_MODEL_PROVIDER": "none",
    "SCHOLAR_LLM_BASE_URL": "",
    "SCHOLAR_LLM_MODEL": "",
    "SCHOLAR_ANTHROPIC_BASE_URL": "https://api.anthropic.com",
    "SCHOLAR_ANTHROPIC_MODEL": "",
}


def read_runtime_config() -> dict[str, str]:
    """Read runtime overrides from PostgreSQL; environment/defaults remain bootstrap inputs."""
    global _RUNTIME_CONFIG_CACHE
    if _RUNTIME_CONFIG_CACHE is not None:
        return dict(_RUNTIME_CONFIG_CACHE)
    try:
        from app.services import mysql_store
        all_settings = mysql_store.get_all_settings()
        _RUNTIME_CONFIG_CACHE = {
            key: str(value) for key, value in all_settings.items()
            if key in CONFIG_KEYS and value is not None
        }
    except Exception:
        _RUNTIME_CONFIG_CACHE = {}
    return dict(_RUNTIME_CONFIG_CACHE)


def write_runtime_config(values: dict[str, Any]) -> dict[str, str]:
    global _RUNTIME_CONFIG_CACHE
    sanitized = _sanitize_values(values, preserve_blank_secrets=False)
    from app.services import mysql_store
    existing = mysql_store.get_all_settings()
    for key in existing:
        if key in CONFIG_KEYS and key not in sanitized:
            mysql_store.execute("DELETE FROM scholar_settings WHERE key = ?", (key,))
    for key, val in sanitized.items():
        mysql_store.set_setting(key, val)
    _RUNTIME_CONFIG_CACHE = dict(sanitized)
    apply_runtime_config(sanitized)
    return sanitized


def update_runtime_config(values: dict[str, Any]) -> dict[str, str]:
    current = read_runtime_config()
    incoming = _sanitize_values(values, preserve_blank_secrets=True)
    merged = dict(current)
    for key, value in incoming.items():
        if key in SECRET_KEYS and value == "":
            continue
        if value == "" and key not in SECRET_KEYS:
            merged.pop(key, None)
            os.environ.pop(key, None)
            continue
        merged[key] = value
    return write_runtime_config(merged)


def apply_runtime_config(values: dict[str, str] | None = None) -> None:
    for key, value in (values or read_runtime_config()).items():
        if key in CONFIG_KEYS:
            os.environ[key] = str(value)


def public_runtime_config() -> dict[str, Any]:
    current = read_runtime_config()
    items: list[dict[str, Any]] = []
    for key in CONFIG_KEYS:
        value = current.get(key, "")
        effective = value if value != "" else os.getenv(key, DEFAULT_VALUES.get(key, ""))
        secret = key in SECRET_KEYS
        items.append(
            {
                "key": key,
                "value": "" if secret else value,
                "effective_value": _mask_secret(effective) if secret else effective,
                "configured": value != "" or bool(os.getenv(key)),
                "secret": secret,
                "options": list(SELECT_OPTIONS.get(key, ())),
            }
        )
    from app.services import mysql_store
    db_path = mysql_store.configured_database_name()
    storage_backend = "postgresql"
    return {
        "path": db_path,
        "storage_backend": storage_backend,
        "items": items,
    }


def _sanitize_values(values: dict[str, Any], preserve_blank_secrets: bool) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in values.items():
        if key not in CONFIG_KEYS:
            continue
        text = _normalize_value(key, value)
        if key in SECRET_KEYS and text == "" and preserve_blank_secrets:
            sanitized[key] = ""
            continue
        options = SELECT_OPTIONS.get(key)
        if options and text and text not in options:
            raise ValueError(f"{key} must be one of: {', '.join(options)}")
        sanitized[key] = text
    return sanitized


def _normalize_value(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value if value is not None else "").strip()
    if key in {
        "SCHOLAR_STORAGE_BACKEND",
        "SCHOLAR_ALLOW_MOCK_DATA",
        "SCHOLAR_EXTERNAL_SOURCE_PROVIDER",
        "SCHOLAR_RAG_INDEX_BACKEND",
        "SCHOLAR_RAG_RETRIEVAL_MODE",
        "SCHOLAR_RAG_EMBEDDING_PROVIDER",
        "SCHOLAR_RAG_CHUNK_STRATEGY",
        "SCHOLAR_PRIMARY_MODEL_PROVIDER",
        "SCHOLAR_SECONDARY_MODEL_PROVIDER",
    }:
        return text.lower()
    return text


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "********"
    return f"{value[:4]}...{value[-4:]}"
