from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp

from app.config import get_settings
from app.services.tracing import now_ms, trace_recorder


OPENAI_COMPATIBLE_PROVIDERS = {
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

ANTHROPIC_PROVIDERS = {"anthropic", "claude"}
LOCAL_OPENAI_COMPATIBLE_PROVIDERS = {"ollama", "vllm", "lmstudio"}


@dataclass(frozen=True)
class ModelResponse:
    content: str
    provider: str
    model: str


class ModelFactory:
    """Model factory with explicit provider selection.

    Mock generation is deliberately opt-in. In normal mode the service either
    calls a configured LLM provider or fails with a visible configuration error.
    """

    async def generate_text(
        self,
        purpose: str,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> ModelResponse:
        settings = get_settings()
        provider = settings.primary_model_provider
        try:
            return await self._generate_with_provider(provider, purpose, prompt, context or {})
        except Exception as primary_error:
            fallback = settings.secondary_model_provider
            if not fallback or fallback == "none" or fallback == provider:
                raise primary_error
            try:
                return await self._generate_with_provider(fallback, purpose, prompt, context or {})
            except Exception as fallback_error:
                raise RuntimeError(
                    f"Primary provider failed: {primary_error}; fallback provider failed: {fallback_error}"
                ) from fallback_error

    async def _generate_with_provider(
        self,
        provider: str,
        purpose: str,
        prompt: str,
        context: dict[str, Any],
    ) -> ModelResponse:
        provider = (provider or "none").lower()
        if provider in {"mock", "deterministic"}:
            if not get_settings().allow_mock_data:
                raise RuntimeError(
                    "Deterministic model output is mock data. Set SCHOLAR_ALLOW_MOCK_DATA=true "
                    "only for tests/demos, or configure SCHOLAR_PRIMARY_MODEL_PROVIDER=openai-compatible."
                )
            started = now_ms()
            content = self._deterministic_response(purpose, prompt, context)
            self._trace_model_call(context, "deterministic", "local-template", now_ms() - started, purpose, True)
            return ModelResponse(
                content=content,
                provider="deterministic",
                model="local-template",
            )
        if provider in OPENAI_COMPATIBLE_PROVIDERS:
            return await self._generate_openai_compatible(provider, purpose, prompt, context)
        if provider in ANTHROPIC_PROVIDERS:
            return await self._generate_anthropic(purpose, prompt, context)
        if provider in {"", "none"}:
            raise RuntimeError(
                "LLM provider is not configured. Set SCHOLAR_PRIMARY_MODEL_PROVIDER=openai-compatible, "
                "SCHOLAR_LLM_BASE_URL, SCHOLAR_LLM_API_KEY, and SCHOLAR_LLM_MODEL."
            )
        raise RuntimeError(
            f"Provider {provider!r} is configured but no adapter is installed"
        )

    async def _generate_openai_compatible(
        self,
        provider: str,
        purpose: str,
        prompt: str,
        context: dict[str, Any],
    ) -> ModelResponse:
        settings = get_settings()
        base_url = (settings.llm_base_url or "https://api.openai.com").rstrip("/")
        model = settings.llm_model
        api_key = settings.llm_api_key
        if not model:
            raise RuntimeError("SCHOLAR_LLM_MODEL is required for LLM calls")
        if not api_key and provider not in LOCAL_OPENAI_COMPATIBLE_PROVIDERS:
            raise RuntimeError("SCHOLAR_LLM_API_KEY is required for remote LLM calls")
        started = now_ms()
        system = (
            "You are ScholarAgent's academic writing worker. Use only the supplied source IDs, "
            "keep claims grounded, and preserve citation IDs exactly."
        )
        user = {
            "purpose": purpose,
            "prompt": prompt,
            "context": context,
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": str(user)},
            ],
            "temperature": 0.2,
        }
        timeout = aiohttp.ClientTimeout(total=60)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{base_url}/v1/chat/completions",
                    json=payload,
                    headers={
                        **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
                        "Content-Type": "application/json",
                    },
                ) as response:
                    data = await response.json(content_type=None)
                    if response.status >= 400:
                        raise RuntimeError(f"LLM provider returned HTTP {response.status}: {data}")
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeError(f"LLM provider response did not include assistant content: {data}") from exc
            self._trace_model_call(context, provider, model, now_ms() - started, purpose, True)
            return ModelResponse(content=content, provider=provider, model=model)
        except Exception:
            self._trace_model_call(context, provider, model, now_ms() - started, purpose, False)
            raise

    async def _generate_anthropic(
        self,
        purpose: str,
        prompt: str,
        context: dict[str, Any],
    ) -> ModelResponse:
        settings = get_settings()
        base_url = (settings.anthropic_base_url or "https://api.anthropic.com").rstrip("/")
        model = settings.anthropic_model or settings.llm_model
        api_key = settings.anthropic_api_key or settings.llm_api_key
        if not api_key or not model:
            raise RuntimeError("SCHOLAR_ANTHROPIC_API_KEY and SCHOLAR_ANTHROPIC_MODEL are required for Claude calls")
        started = now_ms()
        system = (
            "You are ScholarAgent's academic writing worker. Use only the supplied source IDs, "
            "keep claims grounded, and preserve citation IDs exactly."
        )
        payload = {
            "model": model,
            "max_tokens": 1200,
            "temperature": 0.2,
            "system": system,
            "messages": [
                {
                    "role": "user",
                    "content": str({"purpose": purpose, "prompt": prompt, "context": context}),
                }
            ],
        }
        timeout = aiohttp.ClientTimeout(total=60)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{base_url}/v1/messages",
                    json=payload,
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                ) as response:
                    data = await response.json(content_type=None)
                    if response.status >= 400:
                        raise RuntimeError(f"Anthropic provider returned HTTP {response.status}: {data}")
            try:
                content = "".join(item.get("text", "") for item in data.get("content", []) if item.get("type") == "text")
            except AttributeError as exc:
                raise RuntimeError(f"Anthropic response did not include text content: {data}") from exc
            if not content:
                raise RuntimeError(f"Anthropic response did not include text content: {data}")
            self._trace_model_call(context, "anthropic", model, now_ms() - started, purpose, True)
            return ModelResponse(content=content, provider="anthropic", model=model)
        except Exception:
            self._trace_model_call(context, "anthropic", model, now_ms() - started, purpose, False)
            raise

    def _trace_model_call(
        self,
        context: dict[str, Any],
        provider: str,
        model: str,
        latency_ms: int,
        purpose: str,
        ok: bool,
    ) -> None:
        trace_id = str(context.get("trace_id") or "")
        if not trace_id:
            return
        trace_recorder.record(
            trace_id,
            "model.generate_text",
            "model_call",
            task_id=context.get("task_id"),
            tenant_id=context.get("tenant_id"),
            user_id=context.get("user_id"),
            provider=provider,
            model=model,
            latency_ms=latency_ms,
            metadata={"purpose": purpose, "ok": ok},
        )

    def _deterministic_response(self, purpose: str, prompt: str, context: dict[str, Any]) -> str:
        topic = context.get("topic") or "the selected research topic"
        if purpose == "outline":
            return (
                f"# Survey Outline: {topic}\n"
                "## 1. Research Background and Motivation\n"
                "## 2. Method Families and Technical Evolution\n"
                "## 3. Evaluation, Risks, and Open Problems\n"
                "## 4. Future Directions"
            )
        if purpose == "section":
            section_title = context.get("section_title", "Research Background")
            citation_id = context.get("citation_id", "paper:local:seed")
            return (
                f"### {section_title}\n\n"
                f"Recent work on {topic} shows a shift from isolated techniques to "
                f"pipeline-level systems with measurable reliability constraints [{citation_id}]. "
                "The reviewed literature also indicates that deployment quality depends on "
                "data governance, evaluation design, and traceable citation practices."
            )
        if purpose == "critic":
            return "The section is acceptable if every factual claim keeps a source ID and avoids unsupported comparison."
        return prompt[:1200]


model_factory = ModelFactory()
