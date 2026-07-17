import unittest
from unittest.mock import patch

from agents.factory import ModelFactory
from app.services.model_configuration import ModelCandidate


class _FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self, content_type=None):
        return {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }


class _FakeSession:
    last_url = ""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def post(self, url, **kwargs):
        type(self).last_url = url
        return _FakeResponse()


class ModelFactoryEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_compatible_base_url_with_v1_is_not_duplicated(self) -> None:
        candidate = ModelCandidate(
            provider="qwen",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="secret",
            model="qwen3.7-plus",
        )

        with patch("agents.factory.aiohttp.ClientSession", _FakeSession):
            await ModelFactory().probe(candidate, "ScholarAgent probe")

        self.assertEqual(
            _FakeSession.last_url,
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        )


if __name__ == "__main__":
    unittest.main()
