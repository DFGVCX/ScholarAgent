from __future__ import annotations

import unittest

from app.papers.repository import PaperRepository
from app.retrieval.models import RetrievalRequest
from app.retrieval.repository import PostgresRetrievalRepository


class _Mappings:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class _Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def mappings(self):
        return _Mappings(self.rows)


class _Session:
    def __init__(self, results=()) -> None:
        self.results = list(results)
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return self.results.pop(0) if self.results else _Result()


class EmbeddingLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_lexical_query_selects_chunk_index(self) -> None:
        session = _Session([_Result()])
        repository = PostgresRetrievalRepository(session)

        await repository.lexical_candidates(RetrievalRequest("tenant", "user", "query"))

        sql, _ = session.calls[0]
        self.assertIn("c.chunk_index AS chunk_index", sql)

    async def test_chinese_lexical_query_expands_academic_terms(self) -> None:
        session = _Session([_Result()])
        repository = PostgresRetrievalRepository(session)

        await repository.lexical_candidates(
            RetrievalRequest("tenant", "user", "联邦学习是什么")
        )

        sql, params = session.calls[0]
        self.assertIn("c.content ILIKE :alias_pattern_0", sql)
        self.assertEqual(params["alias_pattern_0"], "%federated learning%")

    async def test_vector_query_filters_by_ready_status_and_active_model(self) -> None:
        session = _Session([_Result(), _Result()])
        repository = PostgresRetrievalRepository(session)

        await repository.vector_candidates(
            RetrievalRequest("tenant", "user", "query"),
            [1.0] + [0.0] * 1023,
            "Qwen3-Embedding-4B",
        )

        sql, params = session.calls[1]
        self.assertIn("c.embedding_status='ready'", sql)
        self.assertIn("c.embedding_model=:embedding_model", sql)
        self.assertEqual(params["embedding_model"], "Qwen3-Embedding-4B")

    async def test_mark_stale_clears_incompatible_vectors(self) -> None:
        session = _Session()
        repository = PaperRepository(session)

        await repository.mark_embeddings_stale(
            "tenant", "user", "Qwen3-Embedding-4B"
        )

        sql, params = session.calls[0]
        self.assertIn("embedding=NULL", sql)
        self.assertIn("embedding_status='stale'", sql)
        self.assertIn("embedding_model IS DISTINCT FROM :active_model", sql)
        self.assertEqual(params["active_model"], "Qwen3-Embedding-4B")

    async def test_embedding_stats_return_each_lifecycle_state(self) -> None:
        session = _Session([_Result([{"ready": 3, "stale": 2, "failed": 1, "pending": 4}])])
        repository = PaperRepository(session)

        stats = await repository.embedding_stats("tenant", "user", "active-model")

        self.assertEqual(stats, {"ready": 3, "stale": 2, "failed": 1, "pending": 4})
        sql, params = session.calls[0]
        self.assertIn("embedding_model=:active_model", sql)
        self.assertEqual(params["active_model"], "active-model")

    async def test_reindex_jobs_include_failed_chunks(self) -> None:
        session = _Session([_Result([{"existing": 0}]), _Result()])
        repository = PaperRepository(session)

        await repository.enqueue_reembedding_jobs("tenant", "user")

        self.assertEqual(len(session.calls), 2)
        for sql, _ in session.calls:
            self.assertIn("embedding_status IN ('stale','failed')", sql)

    async def test_reembedding_batch_reconstructs_original_contextual_payload(self) -> None:
        session = _Session(
            [
                _Result(
                    [
                        {
                            "content_uuid": "content-1",
                            "chunk_index": 0,
                            "content": "raw formula",
                            "embedding_content": "definition before\n\nraw formula",
                            "section_path": "2 Method > Equation 1",
                            "section_id": "method",
                            "title": "Test Paper",
                        }
                    ]
                )
            ]
        )

        batch = await PaperRepository(session).current_embedding_batch("tenant", "user", "paper")

        self.assertEqual(
            batch["chunks"][0]["embedding_text"],
            "Paper: Test Paper\nSection: 2 Method > Equation 1\n\ndefinition before\n\nraw formula",
        )
        sql, _ = session.calls[0]
        self.assertIn("c.embedding_content", sql)
        self.assertIn("p.title", sql)


if __name__ == "__main__":
    unittest.main()
