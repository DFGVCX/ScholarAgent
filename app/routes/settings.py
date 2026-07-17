from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from agents.factory import model_factory
from app.config import get_settings
from app.db.session import tenant_transaction
from app.dependencies import AuthError, authenticate_api_key
from app.papers.reembedding import embedding_reindex_service
from app.papers.repository import PaperRepository
from app.retrieval.embedding import QwenEmbeddingClient
from app.services.auth_service import auth_service
from app.services.model_configuration import resolve_embedding_candidate, resolve_model_candidate
from app.services.runtime_config import public_runtime_config, update_runtime_config

router = APIRouter(prefix="/settings", tags=["settings"])

_EMBEDDING_RUNTIME_KEYS = {
    "SCHOLAR_RAG_EMBEDDING_PROVIDER",
    "SCHOLAR_RAG_EMBEDDING_BASE_URL",
    "SCHOLAR_RAG_EMBEDDING_API_KEY",
    "SCHOLAR_RAG_EMBEDDING_MODEL",
    "SCHOLAR_RAG_EMBEDDING_DIMENSIONS",
}


class RuntimeConfigUpdateDTO(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class ModelProbeDTO(BaseModel):
    provider: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    anthropic_base_url: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = ""
    prompt: str = Field(default="用一句中文回答：ScholarAgent 模型接入已连通。", max_length=1000)


class EmbeddingProbeDTO(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    dimensions: int = 1024


def _require_tenant_admin(api_key: str | None) -> dict[str, Any]:
    try:
        user = authenticate_api_key(api_key)
        profile = auth_service.profile_for(user)
    except AuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if "tenant_admin" not in set(profile.get("roles") or []):
        raise HTTPException(status_code=403, detail="Tenant admin role is required")
    return profile


def _embedding_candidate_values(values: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "SCHOLAR_RAG_EMBEDDING_BASE_URL": "base_url",
        "SCHOLAR_RAG_EMBEDDING_API_KEY": "api_key",
        "SCHOLAR_RAG_EMBEDDING_MODEL": "model",
        "SCHOLAR_RAG_EMBEDDING_DIMENSIONS": "dimensions",
    }
    return {target: values[source] for source, target in mapping.items() if source in values}


def _embedding_change_requested(values: dict[str, Any], current: Any) -> bool:
    if not (_EMBEDDING_RUNTIME_KEYS & values.keys()):
        return False
    provider = str(values.get("SCHOLAR_RAG_EMBEDDING_PROVIDER", "qwen") or "qwen").lower()
    if provider != "qwen":
        return True
    checks = (
        ("SCHOLAR_RAG_EMBEDDING_BASE_URL", current.rag_embedding_base_url),
        ("SCHOLAR_RAG_EMBEDDING_MODEL", current.rag_embedding_model),
    )
    if any(key in values and str(values[key]).strip() != str(saved).strip() for key, saved in checks):
        return True
    if "SCHOLAR_RAG_EMBEDDING_DIMENSIONS" in values:
        try:
            if int(values["SCHOLAR_RAG_EMBEDDING_DIMENSIONS"]) != current.rag_embedding_dimensions:
                return True
        except (TypeError, ValueError):
            return True
    return bool(str(values.get("SCHOLAR_RAG_EMBEDDING_API_KEY", "") or "").strip())


async def _probe_embedding_candidate(candidate: Any) -> int:
    try:
        vectors = await QwenEmbeddingClient(**candidate.client_kwargs()).embed(
            ["ScholarAgent embedding probe"]
        )
    except Exception as exc:
        detail = str(exc).replace(candidate.api_key, "***")
        raise HTTPException(status_code=502, detail=detail[:1000]) from exc
    return len(vectors[0])


async def _embedding_stats(profile: dict[str, Any], active_model: str) -> dict[str, int]:
    tenant_id = str(profile["tenant_id"])
    user_id = str(profile["user_id"])
    async with tenant_transaction(tenant_id, user_id) as session:
        return await PaperRepository(session).embedding_stats(tenant_id, user_id, active_model)


@router.get("/runtime")
async def get_runtime_settings(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    profile = _require_tenant_admin(x_api_key)
    active_model = get_settings().rag_embedding_model
    return {
        "profile": profile,
        "config": public_runtime_config(),
        "embedding": {
            "active_model": active_model,
            "dimensions": 1024,
            "counts": await _embedding_stats(profile, active_model),
        },
    }


@router.put("/runtime")
async def update_runtime_settings(
    request: RuntimeConfigUpdateDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    profile = _require_tenant_admin(x_api_key)
    current = get_settings()
    embedding_changed = _embedding_change_requested(request.values, current)
    candidate = None
    try:
        if embedding_changed:
            provider = str(
                request.values.get("SCHOLAR_RAG_EMBEDDING_PROVIDER", "qwen") or "qwen"
            ).lower()
            if provider != "qwen":
                raise ValueError("Qwen is the only supported embedding provider")
            candidate = resolve_embedding_candidate(
                _embedding_candidate_values(request.values), current
            )
            await _probe_embedding_candidate(candidate)
        update_runtime_config(request.values)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    embedding = None
    if candidate is not None:
        tenant_id = str(profile["tenant_id"])
        user_id = str(profile["user_id"])
        force = candidate.base_url.rstrip("/") != current.rag_embedding_base_url.rstrip("/")
        async with tenant_transaction(tenant_id, user_id) as session:
            repository = PaperRepository(session)
            stale = await repository.mark_embeddings_stale(
                tenant_id, user_id, candidate.model, force=force
            )
            counts = await repository.embedding_stats(tenant_id, user_id, candidate.model)
        embedding = {
            "active_model": candidate.model,
            "dimensions": candidate.dimensions,
            "counts": counts,
            "stale_marked": stale,
            "reindex_required": counts["stale"] > 0,
        }
    return {
        "status": "saved",
        "profile": profile,
        "config": public_runtime_config(),
        "embedding": embedding,
        "note": "Runtime settings were saved. Running requests read the updated values on demand.",
    }


@router.post("/model/probe")
async def probe_model(
    request: ModelProbeDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    profile = _require_tenant_admin(x_api_key)
    try:
        candidate = resolve_model_candidate(request.model_dump(exclude={"prompt"}), get_settings())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        response = await model_factory.probe(candidate, request.prompt)
    except Exception as exc:
        detail = str(exc)
        for secret in (candidate.api_key, candidate.anthropic_api_key):
            if secret:
                detail = detail.replace(secret, "***")
        raise HTTPException(status_code=502, detail=detail[:1000]) from exc
    return {
        "status": "ok",
        "profile": profile,
        "provider": response.provider,
        "model": response.model,
        "content": response.content,
    }


@router.post("/embedding/probe")
async def probe_embedding(
    request: EmbeddingProbeDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    profile = _require_tenant_admin(x_api_key)
    try:
        candidate = resolve_embedding_candidate(request.model_dump(), get_settings())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    dimensions = await _probe_embedding_candidate(candidate)
    return {
        "status": "ok",
        "profile": profile,
        "provider": "qwen",
        "model": candidate.model,
        "dimensions": dimensions,
    }


@router.post("/embedding/reindex")
async def reindex_embeddings(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    profile = _require_tenant_admin(x_api_key)
    queued = await embedding_reindex_service.enqueue(
        str(profile["tenant_id"]), str(profile["user_id"])
    )
    return {"status": "queued", "profile": profile, **queued}
