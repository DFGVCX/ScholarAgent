from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


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
    "SCHOLAR_STORAGE_BACKEND": ("auto", "sqlite"),
    "SCHOLAR_ALLOW_MOCK_DATA": ("false", "true"),
    "SCHOLAR_EXTERNAL_SOURCE_PROVIDER": ("real", "mock"),
    "SCHOLAR_RAG_INDEX_BACKEND": ("auto", "chromadb"),
    "SCHOLAR_RAG_RETRIEVAL_MODE": ("hybrid", "lexical", "vector"),
    "SCHOLAR_RAG_EMBEDDING_PROVIDER": ("lexical", "hybrid", "openai-compatible", "bge", "jina", "cohere", "mock-hash"),
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
    "SCHOLAR_STORAGE_BACKEND": "auto",
    "SCHOLAR_ALLOW_MOCK_DATA": "false",
    "SCHOLAR_EXTERNAL_SOURCE_PROVIDER": "real",
    "SCHOLAR_EXTERNAL_SOURCE_TIMEOUT_SECONDS": "8.0",
    "SCHOLAR_RAG_INDEX_BACKEND": "auto",
    "SCHOLAR_RAG_RETRIEVAL_MODE": "hybrid",
    "SCHOLAR_RAG_EMBEDDING_PROVIDER": "lexical",
    "SCHOLAR_RAG_EMBEDDING_BASE_URL": "",
    "SCHOLAR_RAG_EMBEDDING_MODEL": "",
    "SCHOLAR_RAG_EMBEDDING_DIMENSIONS": "0",
    "SCHOLAR_RAG_CHUNK_SIZE": "900",
    "SCHOLAR_RAG_CHUNK_OVERLAP": "120",
    "SCHOLAR_RAG_TOP_K": "8",
    "SCHOLAR_RAG_CANDIDATE_LIMIT": "800",
    "SCHOLAR_PRIMARY_MODEL_PROVIDER": "none",
    "SCHOLAR_SECONDARY_MODEL_PROVIDER": "none",
    "SCHOLAR_LLM_BASE_URL": "",
    "SCHOLAR_LLM_MODEL": "",
    "SCHOLAR_ANTHROPIC_BASE_URL": "https://api.anthropic.com",
    "SCHOLAR_ANTHROPIC_MODEL": "",
}


def runtime_config_path() -> Path:
    configured = os.getenv("SCHOLAR_RUNTIME_CONFIG_PATH")
    if configured:
        return Path(configured)
    storage_dir = Path(os.getenv("SCHOLAR_STORAGE_DIR", "storage/runtime"))
    return storage_dir / "runtime_config.json"


def read_runtime_config() -> dict[str, str]:
    path = runtime_config_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {key: str(value) for key, value in raw.items() if key in CONFIG_KEYS and value is not None}


def write_runtime_config(values: dict[str, Any]) -> dict[str, str]:
    sanitized = _sanitize_values(values, preserve_blank_secrets=False)
    path = runtime_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitized, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
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
    return {
        "path": str(runtime_config_path()),
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
