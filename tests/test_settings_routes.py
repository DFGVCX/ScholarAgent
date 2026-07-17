from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from agents.factory import ModelResponse
from app.routes.settings import (
    EmbeddingProbeDTO,
    ModelProbeDTO,
    RuntimeConfigUpdateDTO,
    probe_embedding,
    probe_model,
    reindex_embeddings,
    update_runtime_settings,
)
from app.schemas import UserContext


class SettingsRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_probe_requires_tenant_admin(self) -> None:
        request = ModelProbeDTO(provider="qwen", api_key="candidate", model="qwen-plus")
        profile = {"tenant_id": "tenant_acme", "user_id": "user_acme", "roles": ["researcher"]}
        with patch(
            "app.routes.settings.authenticate_api_key",
            return_value=UserContext("tenant_acme", "user_acme"),
        ), patch("app.routes.settings.auth_service.profile_for", return_value=profile):
            with self.assertRaises(HTTPException) as caught:
                await probe_model(request, x_api_key="acme-key")

        self.assertEqual(caught.exception.status_code, 403)

    async def test_model_probe_uses_unsaved_candidate_and_never_returns_key(self) -> None:
        request = ModelProbeDTO(
            provider="qwen",
            base_url="https://candidate.example/compatible-mode",
            api_key="candidate-secret",
            model="qwen-plus",
            prompt="reply with ok",
        )
        profile = {"tenant_id": "tenant_demo", "user_id": "user_demo", "roles": ["tenant_admin"]}
        current = SimpleNamespace(
            primary_model_provider="none",
            llm_base_url="",
            llm_api_key="stored-secret",
            llm_model="",
            anthropic_base_url="https://api.anthropic.com",
            anthropic_api_key="",
            anthropic_model="",
        )
        response = ModelResponse("ok", "qwen", "qwen-plus")
        with patch(
            "app.routes.settings.authenticate_api_key",
            return_value=UserContext("tenant_demo", "user_demo"),
        ), patch("app.routes.settings.auth_service.profile_for", return_value=profile), patch(
            "app.routes.settings.get_settings", return_value=current
        ), patch.object(
            __import__("app.routes.settings", fromlist=["model_factory"]).model_factory,
            "probe",
            new=AsyncMock(return_value=response),
        ) as probe:
            result = await probe_model(request, x_api_key="demo-key")

        candidate = probe.await_args.args[0]
        self.assertEqual(candidate.base_url, "https://candidate.example/compatible-mode")
        self.assertEqual(candidate.api_key, "candidate-secret")
        self.assertEqual(result["provider"], "qwen")
        self.assertNotIn("api_key", result)
        self.assertNotIn("candidate-secret", str(result))

    async def test_embedding_probe_uses_candidate_and_reports_dimensions(self) -> None:
        request = EmbeddingProbeDTO(
            base_url="https://candidate.example/compatible-mode",
            api_key="embedding-secret",
            model="Qwen3-Embedding-4B",
            dimensions=1024,
        )
        profile = {"tenant_id": "tenant_demo", "user_id": "user_demo", "roles": ["tenant_admin"]}
        current = SimpleNamespace(
            rag_embedding_base_url="https://saved.example",
            rag_embedding_api_key="saved-secret",
            rag_embedding_model="Qwen3-Embedding-0.6B",
            rag_embedding_dimensions=1024,
        )
        fake_client = SimpleNamespace(embed=AsyncMock(return_value=[[1.0] * 1024]))
        with patch(
            "app.routes.settings.authenticate_api_key",
            return_value=UserContext("tenant_demo", "user_demo"),
        ), patch("app.routes.settings.auth_service.profile_for", return_value=profile), patch(
            "app.routes.settings.get_settings", return_value=current
        ), patch("app.routes.settings.QwenEmbeddingClient", return_value=fake_client) as client_type:
            result = await probe_embedding(request, x_api_key="demo-key")

        self.assertEqual(client_type.call_args.kwargs["model"], "Qwen3-Embedding-4B")
        self.assertEqual(client_type.call_args.kwargs["api_key"], "embedding-secret")
        self.assertEqual(result["dimensions"], 1024)
        self.assertNotIn("embedding-secret", str(result))

    async def test_embedding_activation_probes_before_save_and_marks_old_vectors_stale(self) -> None:
        profile = {"tenant_id": "tenant_demo", "user_id": "user_demo", "roles": ["tenant_admin"]}
        current = SimpleNamespace(
            rag_embedding_base_url="https://old.example/compatible-mode",
            rag_embedding_api_key="saved-secret",
            rag_embedding_model="Qwen3-Embedding-0.6B",
            rag_embedding_dimensions=1024,
        )
        request = RuntimeConfigUpdateDTO(values={
            "SCHOLAR_RAG_EMBEDDING_PROVIDER": "qwen",
            "SCHOLAR_RAG_EMBEDDING_BASE_URL": "https://new.example/compatible-mode",
            "SCHOLAR_RAG_EMBEDDING_API_KEY": "new-secret",
            "SCHOLAR_RAG_EMBEDDING_MODEL": "Qwen3-Embedding-4B",
            "SCHOLAR_RAG_EMBEDDING_DIMENSIONS": "1024",
        })

        class Repository:
            async def mark_embeddings_stale(self, *args, **kwargs):
                self.force = kwargs["force"]
                return 2

            async def embedding_stats(self, *args):
                return {"ready": 0, "stale": 2, "failed": 0, "pending": 0}

        repository = Repository()

        @asynccontextmanager
        async def transaction(*_):
            yield object()

        with patch("app.routes.settings._require_tenant_admin", return_value=profile), patch(
            "app.routes.settings.get_settings", return_value=current
        ), patch(
            "app.routes.settings._probe_embedding_candidate", new=AsyncMock(return_value=1024)
        ) as probe, patch("app.routes.settings.update_runtime_config") as save, patch(
            "app.routes.settings.public_runtime_config", return_value={"items": []}
        ), patch("app.routes.settings.tenant_transaction", transaction), patch(
            "app.routes.settings.PaperRepository", return_value=repository
        ):
            result = await update_runtime_settings(request, x_api_key="demo-key")

        probe.assert_awaited_once()
        save.assert_called_once_with(request.values)
        self.assertTrue(repository.force)
        self.assertTrue(result["embedding"]["reindex_required"])

    async def test_reindex_route_enqueues_for_authenticated_scope(self) -> None:
        profile = {"tenant_id": "tenant_demo", "user_id": "user_demo", "roles": ["tenant_admin"]}
        with patch("app.routes.settings._require_tenant_admin", return_value=profile), patch(
            "app.routes.settings.embedding_reindex_service.enqueue",
            new=AsyncMock(return_value={"created": 2, "existing": 1}),
        ) as enqueue:
            result = await reindex_embeddings(x_api_key="demo-key")

        enqueue.assert_awaited_once_with("tenant_demo", "user_demo")
        self.assertEqual(result["created"], 2)


if __name__ == "__main__":
    unittest.main()
