from __future__ import annotations

import unittest

from app.papers.repository import PaperRepository
from app.retrieval.models import ContextWindowRequest, ParentContextRequest, RetrievalRequest
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
    async def test_lexical_and_vector_queries_share_structured_filters(self) -> None:
        request = RetrievalRequest(
            "tenant",
            "user",
            "query",
            paper_ids=("paper-1",),
            year_from=2020,
            year_to=2024,
            author="Alice",
            venue="NeurIPS",
            section_ids=("method",),
            chunk_types=("equation", "table"),
        )
        session = _Session([_Result(), _Result(), _Result()])
        repository = PostgresRetrievalRepository(session)

        await repository.lexical_candidates(request)
        await repository.vector_candidates(
            request, [1.0] + [0.0] * 1023, "Qwen3-Embedding-4B"
        )

        lexical_sql, lexical_params = session.calls[0]
        vector_sql, vector_params = session.calls[2]
        for sql in (lexical_sql, vector_sql):
            self.assertIn("p.paper_id = ANY", sql)
            self.assertIn("EXTRACT(YEAR FROM p.published_at) >= :year_from", sql)
            self.assertIn("p.authors::text ILIKE :author_pattern", sql)
            self.assertIn("p.metadata->>'venue'", sql)
            self.assertIn("c.section_id = ANY", sql)
            self.assertIn("c.chunk_type = ANY", sql)
        for params in (lexical_params, vector_params):
            self.assertEqual(params["paper_ids"], ["paper-1"])
            self.assertEqual(params["year_from"], 2020)
            self.assertEqual(params["year_to"], 2024)
            self.assertEqual(params["author_pattern"], "%Alice%")
            self.assertEqual(params["venue_pattern"], "%NeurIPS%")

    async def test_parent_context_prefers_parent_section_in_current_version(self) -> None:
        session = _Session(
            [
                _Result(
                    [
                        {
                            "center_chunk_id": "00000000-0000-0000-0000-000000000301",
                            "paper_id": "paper-1",
                            "content_version": 4,
                            "section_id": "method",
                            "title": "2 Method",
                            "kind": "method",
                            "section_path": "2 Method > 2.1 Setup",
                            "page_start": 2,
                            "page_end": 4,
                            "content": "Complete parent section.",
                            "char_count": 24,
                        }
                    ]
                )
            ]
        )
        repository = PostgresRetrievalRepository(session)

        parent = await repository.parent_context(
            ParentContextRequest(
                "tenant", "user", "00000000-0000-0000-0000-000000000301"
            )
        )

        self.assertIsNotNone(parent)
        assert parent is not None
        self.assertEqual(parent.section_id, "method")
        self.assertEqual(parent.content, "Complete parent section.")
        self.assertEqual(parent.paper_id, "paper-1")
        self.assertEqual(parent.content_version, 4)
        sql, params = session.calls[0]
        self.assertIn("p.tenant_id=:tenant_id", sql)
        self.assertIn("p.user_id=:user_id", sql)
        self.assertIn("c.content_version=p.current_content_version", sql)
        self.assertIn("p.in_knowledge_base=true", sql)
        self.assertIn("candidate.section_id=c.parent_section_id", sql)
        self.assertIn("candidate.section_id=c.section_id", sql)
        self.assertIn("p.paper_id", sql)
        self.assertIn("c.content_version", sql)
        self.assertEqual(params["chunk_id"], parent.center_chunk_id)

    async def test_context_window_is_tenant_scoped_and_uses_current_version(self) -> None:
        session = _Session(
            [
                _Result(
                    [
                        {
                            "chunk_id": "00000000-0000-0000-0000-000000000301",
                            "chunk_index": 2,
                            "paper_id": "paper-1",
                            "content_version": 4,
                            "content": "Complete chunk.",
                            "token_count": 4,
                        }
                    ]
                )
            ]
        )
        repository = PostgresRetrievalRepository(session)

        chunks = await repository.context_window(
            ContextWindowRequest(
                "tenant", "user", "00000000-0000-0000-0000-000000000301",
                before=2, after=3, token_budget=1024,
            )
        )

        sql, params = session.calls[0]
        self.assertIn("p.tenant_id=:tenant_id", sql)
        self.assertIn("p.user_id=:user_id", sql)
        self.assertIn("c.content_version=p.current_content_version", sql)
        self.assertIn("p.in_knowledge_base=true", sql)
        self.assertIn("c.chunk_uuid=CAST(:chunk_id AS uuid)", sql)
        self.assertIn("ORDER BY c.chunk_index", sql)
        self.assertIn("p.paper_id", sql)
        self.assertIn("c.content_version", sql)
        self.assertEqual(params["before"], 2)
        self.assertEqual(params["after"], 3)
        self.assertEqual(chunks[0].paper_id, "paper-1")
        self.assertEqual(chunks[0].content_version, 4)

    async def test_lexical_query_selects_chunk_index(self) -> None:
        session = _Session([_Result()])
        repository = PostgresRetrievalRepository(session)

        await repository.lexical_candidates(RetrievalRequest("tenant", "user", "query"))

        sql, _ = session.calls[0]
        self.assertIn("c.chunk_index AS chunk_index", sql)
        self.assertIn("AS previous_chunk_id", sql)
        self.assertIn("AS next_chunk_id", sql)
        self.assertIn("c.context_before", sql)
        self.assertIn("c.context_after", sql)

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
