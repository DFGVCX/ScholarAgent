from __future__ import annotations

import math
import unittest

from app.retrieval.embedding import EmbeddingResponseError, QwenEmbeddingClient


class _Response:
    def __init__(self, payload, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def json(self):
        return self.payload


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def post(self, url, *, json, headers):
        self.request = (url, json, headers)
        return self.response


class QwenEmbeddingTest(unittest.IsolatedAsyncioTestCase):
    async def test_qwen_model_name_can_change_but_dimensions_remain_1024(self) -> None:
        session = _Session(
            _Response({"data": [{"index": 0, "embedding": [1.0] + [0.0] * 1023}]})
        )
        client = QwenEmbeddingClient(
            base_url="https://embedding.example/compatible-mode",
            api_key="secret",
            model="Qwen3-Embedding-4B",
            dimensions=1024,
            session_factory=lambda **_: session,
        )

        await client.embed(["probe"])

        self.assertEqual(session.request[1]["model"], "Qwen3-Embedding-4B")
        self.assertEqual(session.request[1]["dimensions"], 1024)

    def test_non_1024_dimensions_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "1024"):
            QwenEmbeddingClient(
                base_url="https://embedding.example",
                model="Qwen3-Embedding-4B",
                dimensions=768,
            )

    def test_blank_model_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "model"):
            QwenEmbeddingClient(base_url="https://embedding.example", model="")

    async def test_embedding_is_1024_dimensional_and_normalized(self) -> None:
        session = _Session(
            _Response({"data": [{"index": 0, "embedding": [2.0] + [0.0] * 1023}]})
        )
        client = QwenEmbeddingClient(
            base_url="https://embedding.example/compatible-mode",
            api_key="secret",
            session_factory=lambda **_: session,
        )

        vectors = await client.embed(["paper query"])

        self.assertEqual(len(vectors[0]), 1024)
        self.assertTrue(math.isclose(sum(x * x for x in vectors[0]), 1.0))
        self.assertEqual(session.request[0], "https://embedding.example/compatible-mode/v1/embeddings")
        self.assertEqual(session.request[1]["model"], "Qwen3-Embedding-0.6B")
        self.assertEqual(session.request[1]["dimensions"], 1024)

    async def test_wrong_dimension_is_rejected(self) -> None:
        session = _Session(_Response({"data": [{"index": 0, "embedding": [1.0, 2.0]}]}))
        client = QwenEmbeddingClient(
            base_url="https://embedding.example", api_key="secret", session_factory=lambda **_: session
        )
        with self.assertRaisesRegex(EmbeddingResponseError, "1024"):
            await client.embed(["query"])

    async def test_duplicate_response_indexes_are_rejected(self) -> None:
        vector = [1.0] + [0.0] * 1023
        session = _Session(
            _Response({"data": [
                {"index": 0, "embedding": vector},
                {"index": 0, "embedding": vector},
            ]})
        )
        client = QwenEmbeddingClient(
            base_url="https://embedding.example", api_key="secret", session_factory=lambda **_: session
        )
        with self.assertRaisesRegex(EmbeddingResponseError, "indexes"):
            await client.embed(["first", "second"])


if __name__ == "__main__":
    unittest.main()
