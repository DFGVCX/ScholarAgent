from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any

import aiohttp

from app.config import get_settings
from app.services.model_configuration import (
    ANTHROPIC_PROVIDERS,
    DETERMINISTIC_PROVIDERS,
    LOCAL_OPENAI_COMPATIBLE_PROVIDERS,
    OPENAI_COMPATIBLE_PROVIDERS,
    ModelCandidate,
)
from app.services.tracing import now_ms, trace_recorder
from agents.runtime.token_policy import ModelCallBudget, token_policy


@dataclass(frozen=True)
class ModelResponse:
    content: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False


class ModelFactory:
    """Model factory with explicit provider selection.

    Mock generation is deliberately opt-in. In normal mode the service either
    calls a configured LLM provider or fails with a visible configuration error.
    """

    def __init__(self) -> None:
        self._cache: OrderedDict[str, tuple[float, ModelResponse]] = OrderedDict()

    async def probe(self, candidate: ModelCandidate, prompt: str) -> ModelResponse:
        """Test candidate settings without changing environment or persisted config."""
        candidate.validate()
        prepared_prompt, prepared_context, budget, estimated_input = token_policy.prepare(
            "config_probe",
            prompt,
            {"tenant_id": "settings-probe", "user_id": "settings-probe"},
        )
        return await self._generate_with_candidate(
            candidate,
            "config_probe",
            prepared_prompt,
            prepared_context,
            budget,
            estimated_input,
        )

    async def generate_text(
        self,
        purpose: str,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> ModelResponse:
        settings = get_settings()
        provider = settings.primary_model_provider
        prepared_prompt, prepared_context, budget, estimated_input = token_policy.prepare(
            purpose, prompt, context or {}
        )
        cache_key = self._cache_key(provider, purpose, prepared_prompt, prepared_context)
        cached = self._cache_get(cache_key, budget.cache_ttl_seconds)
        if settings.model_response_cache_enabled and cached is not None:
            self._trace_model_call(
                prepared_context, cached.provider, cached.model, 0, purpose, True,
                input_tokens=0, output_tokens=0, cached=True,
            )
            return cached
        try:
            response = await self._generate_with_provider(
                provider, purpose, prepared_prompt, prepared_context, budget, estimated_input
            )
        except Exception as primary_error:
            fallback = settings.secondary_model_provider
            if not fallback or fallback == "none" or fallback == provider:
                raise primary_error
            try:
                response = await self._generate_with_provider(
                    fallback, purpose, prepared_prompt, prepared_context, budget, estimated_input
                )
            except Exception as fallback_error:
                raise RuntimeError(
                    f"Primary provider failed: {primary_error}; fallback provider failed: {fallback_error}"
                ) from fallback_error
        if settings.model_response_cache_enabled and budget.cache_ttl_seconds > 0:
            self._cache_put(cache_key, response, settings.model_response_cache_max_entries)
        return response

    async def _generate_with_provider(
        self,
        provider: str,
        purpose: str,
        prompt: str,
        context: dict[str, Any],
        budget: ModelCallBudget,
        estimated_input: int,
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
            self._trace_model_call(
                context, "deterministic", "local-template", now_ms() - started, purpose, True,
                input_tokens=estimated_input,
                output_tokens=token_policy.estimate_tokens(content),
            )
            return ModelResponse(
                content=content,
                provider="deterministic",
                model="local-template",
                input_tokens=estimated_input,
                output_tokens=token_policy.estimate_tokens(content),
            )
        if provider in OPENAI_COMPATIBLE_PROVIDERS:
            return await self._generate_openai_compatible(provider, purpose, prompt, context, budget, estimated_input)
        if provider in ANTHROPIC_PROVIDERS:
            return await self._generate_anthropic(purpose, prompt, context, budget, estimated_input)
        if provider in {"", "none"}:
            raise RuntimeError(
                "LLM provider is not configured. Set SCHOLAR_PRIMARY_MODEL_PROVIDER=openai-compatible, "
                "SCHOLAR_LLM_BASE_URL, SCHOLAR_LLM_API_KEY, and SCHOLAR_LLM_MODEL."
            )
        raise RuntimeError(
            f"Provider {provider!r} is configured but no adapter is installed"
        )

    async def _generate_with_candidate(
        self,
        candidate: ModelCandidate,
        purpose: str,
        prompt: str,
        context: dict[str, Any],
        budget: ModelCallBudget,
        estimated_input: int,
    ) -> ModelResponse:
        provider = candidate.provider.strip().lower()
        if provider in DETERMINISTIC_PROVIDERS:
            if not get_settings().allow_mock_data:
                raise RuntimeError("Deterministic model probes are disabled outside tests/demos")
            content = self._deterministic_response(purpose, prompt, context)
            return ModelResponse(
                content=content,
                provider="deterministic",
                model="local-template",
                input_tokens=estimated_input,
                output_tokens=token_policy.estimate_tokens(content),
            )
        if provider in OPENAI_COMPATIBLE_PROVIDERS:
            return await self._generate_openai_compatible(
                provider,
                purpose,
                prompt,
                context,
                budget,
                estimated_input,
                candidate=candidate,
            )
        if provider in ANTHROPIC_PROVIDERS:
            return await self._generate_anthropic(
                purpose,
                prompt,
                context,
                budget,
                estimated_input,
                candidate=candidate,
            )
        raise RuntimeError(f"Provider {provider!r} is configured but no adapter is installed")

    async def _generate_openai_compatible(
        self,
        provider: str,
        purpose: str,
        prompt: str,
        context: dict[str, Any],
        budget: ModelCallBudget,
        estimated_input: int,
        candidate: ModelCandidate | None = None,
    ) -> ModelResponse:
        settings = get_settings()
        base_url = (
            (candidate.base_url if candidate is not None else settings.llm_base_url)
            or "https://api.openai.com"
        ).rstrip("/")
        model = candidate.model if candidate is not None else settings.llm_model
        api_key = candidate.api_key if candidate is not None else settings.llm_api_key
        if not model:
            raise RuntimeError("SCHOLAR_LLM_MODEL is required for LLM calls")
        if not api_key and provider not in LOCAL_OPENAI_COMPATIBLE_PROVIDERS:
            raise RuntimeError("SCHOLAR_LLM_API_KEY is required for remote LLM calls")
        started = now_ms()
        structured_planning = purpose in {"intent_planning", "tool_planning"}
        system = (
            "You are ScholarAgent's coordinator agent. Resolve the user's semantic goal from "
            "conversation state and return only the requested JSON object. Never mix command words "
            "into a literature-search subject and never invent tools."
            if structured_planning
            else (
                "You are ScholarAgent's academic writing worker. Use only the supplied source IDs, "
                "keep claims grounded, and preserve citation IDs exactly."
            )
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
            "temperature": 0 if structured_planning else 0.2,
            "max_tokens": budget.max_output_tokens,
        }
        if structured_planning:
            payload["response_format"] = {"type": "json_object"}
        timeout = aiohttp.ClientTimeout(total=60)
        completions_url = (
            f"{base_url}/chat/completions"
            if base_url.endswith("/v1")
            else f"{base_url}/v1/chat/completions"
        )
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    completions_url,
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
            usage = data.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens") or estimated_input)
            output_tokens = int(usage.get("completion_tokens") or token_policy.estimate_tokens(content))
            self._trace_model_call(
                context, provider, model, now_ms() - started, purpose, True,
                input_tokens=input_tokens, output_tokens=output_tokens,
            )
            return ModelResponse(content=content, provider=provider, model=model, input_tokens=input_tokens, output_tokens=output_tokens)
        except Exception:
            self._trace_model_call(context, provider, model, now_ms() - started, purpose, False, input_tokens=estimated_input)
            raise

    async def _generate_anthropic(
        self,
        purpose: str,
        prompt: str,
        context: dict[str, Any],
        budget: ModelCallBudget,
        estimated_input: int,
        candidate: ModelCandidate | None = None,
    ) -> ModelResponse:
        settings = get_settings()
        if candidate is None:
            base_url = (settings.anthropic_base_url or "https://api.anthropic.com").rstrip("/")
            model = settings.anthropic_model or settings.llm_model
            api_key = settings.anthropic_api_key or settings.llm_api_key
        else:
            base_url = (
                candidate.anthropic_base_url
                or candidate.base_url
                or "https://api.anthropic.com"
            ).rstrip("/")
            model = candidate.anthropic_model or candidate.model
            api_key = candidate.anthropic_api_key or candidate.api_key
        if not api_key or not model:
            raise RuntimeError("SCHOLAR_ANTHROPIC_API_KEY and SCHOLAR_ANTHROPIC_MODEL are required for Claude calls")
        started = now_ms()
        structured_planning = purpose in {"intent_planning", "tool_planning"}
        system = (
            "You are ScholarAgent's coordinator agent. Resolve the user's semantic goal from "
            "conversation state and return only one valid JSON object. Never mix command words "
            "into a literature-search subject and never invent tools."
            if structured_planning
            else (
                "You are ScholarAgent's academic writing worker. Use only the supplied source IDs, "
                "keep claims grounded, and preserve citation IDs exactly."
            )
        )
        payload = {
            "model": model,
            "max_tokens": budget.max_output_tokens,
            "temperature": 0 if structured_planning else 0.2,
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
            usage = data.get("usage") or {}
            input_tokens = int(usage.get("input_tokens") or estimated_input)
            output_tokens = int(usage.get("output_tokens") or token_policy.estimate_tokens(content))
            self._trace_model_call(
                context, "anthropic", model, now_ms() - started, purpose, True,
                input_tokens=input_tokens, output_tokens=output_tokens,
            )
            return ModelResponse(content=content, provider="anthropic", model=model, input_tokens=input_tokens, output_tokens=output_tokens)
        except Exception:
            self._trace_model_call(context, "anthropic", model, now_ms() - started, purpose, False, input_tokens=estimated_input)
            raise

    def _trace_model_call(
        self,
        context: dict[str, Any],
        provider: str,
        model: str,
        latency_ms: int,
        purpose: str,
        ok: bool,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached: bool = False,
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
            metadata={
                "purpose": purpose, "ok": ok, "cached": cached,
                "input_tokens": input_tokens, "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
        )

    @staticmethod
    def _cache_key(provider: str, purpose: str, prompt: str, context: dict[str, Any]) -> str:
        scope = {
            "tenant_id": context.get("tenant_id"),
            "user_id": context.get("user_id"),
            "provider": provider,
            "purpose": purpose,
            "prompt": prompt,
            "context": context,
        }
        payload = json.dumps(scope, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str, ttl_seconds: int) -> ModelResponse | None:
        if ttl_seconds <= 0 or key not in self._cache:
            return None
        created_at, response = self._cache[key]
        if time.monotonic() - created_at > ttl_seconds:
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return ModelResponse(
            response.content, response.provider, response.model,
            input_tokens=0, output_tokens=0, cached=True,
        )

    def _cache_put(self, key: str, response: ModelResponse, max_entries: int) -> None:
        self._cache[key] = (time.monotonic(), response)
        self._cache.move_to_end(key)
        while len(self._cache) > max_entries:
            self._cache.popitem(last=False)

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
