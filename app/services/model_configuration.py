from __future__ import annotations

from dataclasses import dataclass
from typing import Any


OPENAI_COMPATIBLE_PROVIDERS = frozenset(
    {
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
        "gemini",
        "cohere",
    }
)
ANTHROPIC_PROVIDERS = frozenset({"anthropic", "claude"})
LOCAL_OPENAI_COMPATIBLE_PROVIDERS = frozenset({"ollama", "vllm", "lmstudio"})
DETERMINISTIC_PROVIDERS = frozenset({"mock", "deterministic"})


@dataclass(frozen=True)
class ModelCandidate:
    provider: str
    base_url: str
    api_key: str
    model: str
    anthropic_base_url: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = ""

    def validate(self) -> "ModelCandidate":
        provider = self.provider.strip().lower()
        if provider in {"", "none"}:
            raise ValueError("Select a model provider")
        if provider not in (
            OPENAI_COMPATIBLE_PROVIDERS | ANTHROPIC_PROVIDERS | DETERMINISTIC_PROVIDERS
        ):
            raise ValueError(f"Unsupported model provider: {provider}")
        if provider in OPENAI_COMPATIBLE_PROVIDERS:
            if not self.model.strip():
                raise ValueError("Model name is required")
            if provider not in LOCAL_OPENAI_COMPATIBLE_PROVIDERS and not self.api_key:
                raise ValueError("API key is required for a remote model provider")
        if provider in ANTHROPIC_PROVIDERS:
            if not (self.anthropic_model or self.model).strip():
                raise ValueError("Model name is required")
            if not (self.anthropic_api_key or self.api_key):
                raise ValueError("API key is required for Anthropic")
        return self


def resolve_model_candidate(values: dict[str, Any], settings: Any) -> ModelCandidate:
    """Build a non-persistent candidate, preserving only blank secret fields."""

    def value(name: str, current_name: str) -> str:
        if name not in values:
            return str(getattr(settings, current_name, "") or "").strip()
        return str(values.get(name) or "").strip()

    def secret(name: str, current_name: str) -> str:
        submitted = str(values.get(name) or "").strip() if name in values else ""
        if submitted:
            return submitted
        return str(getattr(settings, current_name, "") or "").strip()

    candidate = ModelCandidate(
        provider=value("provider", "primary_model_provider").lower(),
        base_url=value("base_url", "llm_base_url"),
        api_key=secret("api_key", "llm_api_key"),
        model=value("model", "llm_model"),
        anthropic_base_url=value("anthropic_base_url", "anthropic_base_url"),
        anthropic_api_key=secret("anthropic_api_key", "anthropic_api_key"),
        anthropic_model=value("anthropic_model", "anthropic_model"),
    )
    return candidate.validate()
