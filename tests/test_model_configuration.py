from types import SimpleNamespace
import unittest

from app.services.model_configuration import ModelCandidate, resolve_model_candidate


class ModelConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current = SimpleNamespace(
            primary_model_provider="qwen",
            llm_base_url="https://dashscope.example/compatible-mode",
            llm_api_key="stored-secret",
            llm_model="qwen-plus",
            anthropic_base_url="https://api.anthropic.com",
            anthropic_api_key="stored-anthropic-secret",
            anthropic_model="claude-sonnet",
        )

    def test_blank_candidate_key_reuses_configured_secret(self) -> None:
        candidate = resolve_model_candidate({"api_key": ""}, self.current)

        self.assertEqual(candidate.api_key, "stored-secret")
        self.assertEqual(candidate.model, "qwen-plus")

    def test_candidate_values_do_not_mutate_current_settings(self) -> None:
        candidate = resolve_model_candidate(
            {"provider": "deepseek", "model": "deepseek-chat", "api_key": "new-secret"},
            self.current,
        )

        self.assertEqual(candidate.provider, "deepseek")
        self.assertEqual(candidate.model, "deepseek-chat")
        self.assertEqual(self.current.primary_model_provider, "qwen")
        self.assertEqual(self.current.llm_api_key, "stored-secret")

    def test_remote_candidate_requires_model_and_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "API key"):
            ModelCandidate(
                provider="qwen",
                base_url="https://example.test",
                api_key="",
                model="qwen-plus",
            ).validate()

        with self.assertRaisesRegex(ValueError, "Model name"):
            ModelCandidate(
                provider="qwen",
                base_url="https://example.test",
                api_key="secret",
                model="",
            ).validate()

    def test_local_openai_compatible_candidate_does_not_require_key(self) -> None:
        candidate = ModelCandidate(
            provider="ollama",
            base_url="http://host.docker.internal:11434",
            api_key="",
            model="qwen3:8b",
        )

        self.assertIs(candidate.validate(), candidate)


if __name__ == "__main__":
    unittest.main()
