from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from agents.factory import ModelResponse
from app.routes.settings import ModelProbeDTO, probe_model
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


if __name__ == "__main__":
    unittest.main()
