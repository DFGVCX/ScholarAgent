from __future__ import annotations

import unittest

from app.retrieval.reranker import QwenRerankerClient, RerankUnavailable


class _Response:
    def __init__(self, payload, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def json(self, **_):
        return self.payload


class _Session:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def post(self, url, *, json, headers):
        self.requests.append((url, json, headers))
        return self.responses.pop(0)


class QwenRerankerTest(unittest.IsolatedAsyncioTestCase):
    async def test_native_payload_and_response_are_mapped_to_original_indexes(self) -> None:
        session = _Session(
            [_Response({
                "request_id": "request-1",
                "output": {"results": [
                    {"index": 1, "relevance_score": 0.91},
                    {"index": 0, "relevance_score": 0.42},
                ]},
                "usage": {"total_tokens": 37},
            })]
        )
        client = QwenRerankerClient(
            endpoint="https://example.test/api/v1/services/rerank/text-rerank/text-rerank",
            api_key="secret",
            model="qwen3.7-text-rerank",
            session_factory=lambda **_: session,
        )

        results = await client.rerank("query", ["first", "second"], top_n=2)

        self.assertEqual([(item.index, item.score) for item in results], [(1, 0.91), (0, 0.42)])
        payload = session.requests[0][1]
        self.assertEqual(payload["input"]["documents"], ["first", "second"])
        self.assertEqual(payload["parameters"]["top_n"], 2)
        self.assertEqual(client.last_usage.reported_tokens, 37)

    async def test_qwen3_compatible_payload_uses_root_level_fields(self) -> None:
        session = _Session([_Response({"results": [{"index": 0, "relevance_score": 0.8}]})])
        client = QwenRerankerClient(
            endpoint="https://workspace.example/compatible-api/v1/reranks",
            api_key="secret",
            model="qwen3-rerank",
            session_factory=lambda **_: session,
        )

        await client.rerank("query", ["document"], top_n=1)

        payload = session.requests[0][1]
        self.assertEqual(payload["query"], "query")
        self.assertNotIn("input", payload)

    async def test_missing_key_is_an_explicit_unavailable_error(self) -> None:
        client = QwenRerankerClient(endpoint="https://example.test/rerank")

        with self.assertRaisesRegex(RerankUnavailable, "API key"):
            await client.rerank("query", ["document"], top_n=1)
