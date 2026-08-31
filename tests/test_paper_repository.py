from __future__ import annotations

import unittest
from uuid import UUID

from app.papers.chunking import ChunkDraft
from app.papers.models import PaperInput, normalize_arxiv_id, normalize_doi
from app.papers.parsing import ParsedBlock, ParsedPage, ParsedPaper, ParsedSection
from app.papers.repository import PaperRepository


class _Mappings:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class _Result:
    def __init__(self, rows=None):
        self.rows = rows

    def mappings(self):
        return _Mappings(self.rows)


class _WriteMappings:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class _WriteResult:
    def __init__(self, row=None, scalar=None):
        self.row = row
        self.scalar = scalar

    def mappings(self):
        return _WriteMappings(self.row)

    def scalar_one(self):
        return self.scalar


class _Session:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), params or {}))
        return _Result()


def _metadata_update_execute(session):
    async def execute(statement, params=None):
        session.statements.append((str(statement), params or {}))
        return _WriteResult(row={"paper_uuid": UUID("00000000-0000-0000-0000-000000000111")})

    return execute


class _StructureSession(_Session):
    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, params or {}))
        if "FROM paper_contents pc" in sql:
            return _Result(
                [
                    {
                        "paper_id": "paper-1",
                        "content_uuid": UUID("00000000-0000-0000-0000-000000000111"),
                        "content_version": 2,
                        "parser_name": "multimodal_aware_v3",
                        "parser_version": "3",
                        "chunk_strategy": "scholar_hierarchical_v4",
                        "chunker_version": "4",
                        "parse_status": "ready",
                        "parse_manifest": {
                            "visual_blocks": [{"block_type": "figure"}],
                            "asset_inventory": [
                                {
                                    "name": "page_001_figure_1.png",
                                    "type": "figure",
                                    "page_number": 1,
                                }
                            ],
                        },
                    }
                ]
            )
        if "FROM paper_pages pp" in sql:
            return _Result(
                [
                    {
                        "page_number": 1,
                        "text": "Figure 1. Architecture",
                        "quality_status": "usable",
                        "extraction_method": "pymupdf_multimodal",
                        "blocks": [{"block_type": "figure", "metadata": {"label": "Figure 1"}}],
                    }
                ]
            )
        if "FROM paper_sections ps" in sql:
            return _Result(
                [
                    {
                        "section_id": "method",
                        "section_index": 0,
                        "kind": "method",
                        "title": "2 Method",
                        "page_start": 1,
                        "page_end": 1,
                        "content": "Figure 1. Architecture",
                    }
                ]
            )
        if "FROM paper_chunks pc" in sql:
            return _Result(
                [
                    {
                        "chunk_uuid": UUID("00000000-0000-0000-0000-000000000301"),
                        "chunk_index": 0,
                        "chunk_type": "prose",
                        "section_id": "method",
                        "section_path": "2 Method > 2.1 Setup",
                        "parent_section_id": "method",
                        "page_start": 1,
                        "page_end": 2,
                        "content": "Complete first chunk.\nIt keeps every character.",
                        "embedding_content": "Paper: Test\nSection: Method\n\nComplete first chunk.",
                        "token_count": 9,
                        "source_block_ids": ["page-1-block-2", "page-2-block-1"],
                        "chunk_metadata": {"strategy": "scholar_hierarchical_v4"},
                        "context_before": "Previous definition.",
                        "context_after": "Following explanation.",
                        "previous_chunk_id": None,
                        "next_chunk_id": UUID("00000000-0000-0000-0000-000000000302"),
                        "embedding_status": "ready",
                        "embedding_model": "qwen3.7-text-embedding",
                    },
                    {
                        "chunk_uuid": UUID("00000000-0000-0000-0000-000000000302"),
                        "chunk_index": 1,
                        "chunk_type": "equation",
                        "section_id": "method",
                        "section_path": "2 Method > Equation 1",
                        "parent_section_id": "method",
                        "page_start": 2,
                        "page_end": 2,
                        "content": "$$F(x)=x^2$$",
                        "embedding_content": "Equation: F(x)=x^2",
                        "token_count": 6,
                        "source_block_ids": ["page-2-equation-1"],
                        "chunk_metadata": {"strategy": "scholar_hierarchical_v4"},
                        "context_before": "Equation introduction.",
                        "context_after": "Equation interpretation.",
                        "previous_chunk_id": UUID("00000000-0000-0000-0000-000000000301"),
                        "next_chunk_id": None,
                        "embedding_status": "ready",
                        "embedding_model": "qwen3.7-text-embedding",
                    },
                ]
            )
        return _Result()


class _WriteSession(_Session):
    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, params or {}))
        if "SELECT current_content_version" in sql:
            return _WriteResult({"current_content_version": 0})
        if "INSERT INTO paper_contents" in sql:
            return _WriteResult(scalar=UUID("00000000-0000-0000-0000-000000000222"))
        return _WriteResult()


class _StatsSession(_Session):
    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, params or {}))
        return _Result(
            [
                {
                    "paper_count": 7,
                    "chunk_count": 396,
                    "vector_count": 319,
                    "failed_papers": 1,
                    "failed_jobs": 2,
                    "pending_jobs": 3,
                    "ready_noncurrent_chunks": 4,
                    "ready_missing_vectors": 5,
                    "ready_wrong_model": 6,
                    "chunk_table_bytes": 1024,
                    "chunk_index_bytes": 512,
                    "embedding_call_count": 12,
                    "embedding_success_count": 9,
                    "embedding_failed_count": 3,
                    "embedding_request_count": 15,
                    "embedding_successful_request_count": 11,
                    "embedding_failed_request_count": 4,
                    "embedding_cancelled_request_count": 1,
                    "embedding_reported_tokens": 12345,
                    "embedding_usage_reported_requests": 11,
                    "embedding_successful_usage_reported_requests": 9,
                }
            ]
        )


class PaperRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_update_bibliography_changes_only_tenant_scoped_paper_metadata(self) -> None:
        session = _Session()
        session.execute = _metadata_update_execute(session)  # type: ignore[method-assign]

        updated = await PaperRepository(session).update_bibliography(
            "tenant-a",
            "user-a",
            "paper-1",
            title="Corrected title",
            authors=("Alice", "Bob"),
            published_at="2025-01-02",
            doi="https://doi.org/10.1000/ABC.1",
            arxiv_id="arXiv:2401.12345v2",
            metadata={"bibliography": {"title": {"value": "Corrected title"}}},
        )

        self.assertTrue(updated)
        sql, params = session.statements[-1]
        self.assertIn("UPDATE papers", sql)
        self.assertNotIn("paper_contents", sql)
        self.assertNotIn("paper_chunks", sql)
        self.assertNotIn("current_content_version=", sql)
        self.assertIn("tenant_id=:tenant_id", sql)
        self.assertIn("user_id=:user_id", sql)
        self.assertIn("deleted_at IS NULL", sql)
        self.assertEqual(params["tenant_id"], "tenant-a")
        self.assertEqual(params["user_id"], "user-a")
        self.assertEqual(params["doi"], "10.1000/abc.1")
        self.assertEqual(params["arxiv_id"], "2401.12345")

    async def test_stats_report_capacity_jobs_and_ready_vector_consistency(self) -> None:
        session = _StatsSession()

        stats = await PaperRepository(session).stats(
            "tenant-a", "user-a", active_model="qwen-active"
        )

        self.assertEqual(stats["paper_count"], 7)
        self.assertEqual(stats["vector_count"], 319)
        self.assertEqual(stats["ready_noncurrent_chunks"], 4)
        self.assertEqual(stats["ready_missing_vectors"], 5)
        self.assertEqual(stats["ready_wrong_model"], 6)
        self.assertEqual(stats["chunk_index_bytes"], 512)
        self.assertEqual(stats["embedding_call_count"], 12)
        self.assertEqual(stats["embedding_reported_tokens"], 12345)
        sql, params = session.statements[0]
        self.assertIn("COUNT(DISTINCT p.paper_uuid)", sql)
        self.assertIn("pg_total_relation_size", sql)
        self.assertIn("pg_indexes_size", sql)
        self.assertIn("paper_ingestion_jobs", sql)
        self.assertIn("c.content_version<>p.current_content_version", sql)
        self.assertIn("c.embedding IS NULL", sql)
        self.assertIn("c.embedding_model IS DISTINCT FROM :active_model", sql)
        self.assertIn("embedding_usage_events", sql)
        self.assertEqual(params["active_model"], "qwen-active")

    async def test_embedding_usage_event_is_written_with_tenant_scope(self) -> None:
        session = _Session()

        await PaperRepository(session).record_embedding_usage(
            "tenant-a",
            "user-a",
            operation="retrieval",
            model="qwen3.7-text-embedding",
            status="succeeded",
            input_count=1,
            request_count=2,
            successful_request_count=1,
            failed_request_count=1,
            cancelled_request_count=0,
            reported_tokens=8,
            usage_reported_requests=1,
            successful_usage_reported_requests=1,
            duration_ms=25,
            error_type=None,
        )

        sql, params = session.statements[-1]
        self.assertIn("INSERT INTO embedding_usage_events", sql)
        self.assertEqual(params["tenant_id"], "tenant-a")
        self.assertEqual(params["user_id"], "user-a")
        self.assertEqual(params["operation"], "retrieval")
        self.assertNotIn("error_message", params)

    async def test_get_structure_exposes_current_chunk_strategy(self) -> None:
        structure = await PaperRepository(_StructureSession()).get_structure(
            "tenant-a", "user-a", "paper-1"
        )

        self.assertIsNotNone(structure)
        assert structure is not None
        self.assertEqual(
            structure["chunker"],
            {"strategy": "scholar_hierarchical_v4", "version": "4"},
        )

    async def test_get_structure_returns_current_ordered_pages_and_sections(self) -> None:
        session = _StructureSession()

        structure = await PaperRepository(session).get_structure("tenant-a", "user-a", "paper-1")

        self.assertIsNotNone(structure)
        assert structure is not None
        self.assertEqual(structure["parser"]["name"], "multimodal_aware_v3")
        self.assertEqual(structure["pages"][0]["blocks"][0]["block_type"], "figure")
        self.assertEqual(
            structure["assets"],
            [{"name": "page_001_figure_1.png", "type": "figure", "page_number": 1}],
        )
        self.assertEqual(structure["sections"][0]["section_id"], "method")
        self.assertEqual([chunk["index"] for chunk in structure["chunks"]], [0, 1])
        self.assertEqual(
            structure["chunks"][0]["content"],
            "Complete first chunk.\nIt keeps every character.",
        )
        self.assertEqual(structure["chunks"][1]["type"], "equation")
        self.assertEqual(structure["chunks"][0]["parent_section_id"], "method")
        self.assertEqual(structure["chunks"][0]["context_before"], "Previous definition.")
        self.assertEqual(
            structure["chunks"][0]["next_chunk_id"],
            "00000000-0000-0000-0000-000000000302",
        )
        self.assertEqual(structure["chunks"][0]["source_block_ids"], ["page-1-block-2", "page-2-block-1"])
        chunk_sql = next(sql for sql, _ in session.statements if "FROM paper_chunks pc" in sql)
        self.assertIn("ORDER BY pc.chunk_index", chunk_sql)
        self.assertIn("LAG(pc.chunk_uuid)", chunk_sql)
        self.assertIn("LEAD(pc.chunk_uuid)", chunk_sql)
        self.assertTrue(all(params["tenant_id"] == "tenant-a" for _, params in session.statements))
        self.assertTrue(all(params["user_id"] == "user-a" for _, params in session.statements))
        self.assertIn("current_content_version", session.statements[0][0])

    async def test_get_requires_tenant_user_and_not_deleted(self) -> None:
        session = _Session()
        paper = await PaperRepository(session).get("tenant-a", "user-a", "paper-1")

        self.assertIsNone(paper)
        sql, params = session.statements[-1]
        self.assertIn("tenant_id", sql)
        self.assertIn("user_id", sql)
        self.assertIn("deleted_at IS NULL", sql)
        self.assertEqual(params["tenant_id"], "tenant-a")
        self.assertEqual(params["user_id"], "user-a")

    def test_identifier_normalization(self) -> None:
        self.assertEqual(normalize_doi(" https://doi.org/10.1000/ABC.1 "), "10.1000/abc.1")
        self.assertEqual(normalize_arxiv_id("arXiv:2401.12345v2"), "2401.12345")

    def test_paper_input_rejects_empty_identity(self) -> None:
        with self.assertRaises(ValueError):
            PaperInput(paper_id="", source="manual", title="A paper")

    async def test_replace_content_persists_pages_sections_and_chunk_provenance(self) -> None:
        session = _WriteSession()
        block = ParsedBlock(1, "body", "Method paragraph.", (1.0, 2.0, 3.0, 4.0), 0, 11.0)
        page = ParsedPage(1, "Method paragraph.", "page-hash", 16, "pymupdf_layout", "usable", (block,))
        section = ParsedSection(
            "method", 0, "method", "2 Method", 1, 1,
            "Method paragraph.", 0, 17, "section-hash",
        )
        parsed = ParsedPaper(
            full_text="2 Method\n\nMethod paragraph.",
            pages=(page,),
            sections=(section,),
            metadata={"language": "en"},
            manifest={"coverage": {"total_pages": 1, "pages_extracted": 1}},
            status="ready",
            quality_score=0.95,
        )
        chunk = ChunkDraft(
            position=0,
            content="Method paragraph.",
            content_hash="chunk-hash",
            token_count=4,
            section_id="method",
            section_path="2 Method",
            page_start=1,
            page_end=1,
            char_start=0,
            char_end=17,
            chunk_type="equation",
            parent_section_id="method",
            source_block_ids=("eq-1",),
            context_before="Definitions before.",
            context_after="Explanation after.",
            embedding_content="Definitions before. Equation. Explanation after.",
            metadata={"provenance": {"page_number": 1}},
        )
        code_chunk = ChunkDraft(
            position=1,
            content="model.fit(local_data)",
            content_hash="code-hash",
            token_count=6,
            section_id="method",
            section_path="2 Method",
            page_start=1,
            page_end=1,
            chunk_type="code",
            source_block_ids=("code-1",),
            embedding_content="model.fit(local_data)",
        )

        version = await PaperRepository(session).replace_content(
            "tenant-a",
            "user-a",
            UUID("00000000-0000-0000-0000-000000000111"),
            parsed.full_text,
            "content-hash",
            [chunk, code_chunk],
            extraction_method="pymupdf_layout",
            parsed=parsed,
            parser_name="structure_aware_v1",
            parser_version="1",
            chunk_strategy="structure_aware_v1",
            chunker_version="1",
        )

        self.assertEqual(version.content_version, 1)
        content_params = next(params for sql, params in session.statements if "INSERT INTO paper_contents" in sql)
        page_params = next(params for sql, params in session.statements if "INSERT INTO paper_pages" in sql)
        section_params = next(params for sql, params in session.statements if "INSERT INTO paper_sections" in sql)
        chunk_params_list = [
            params for sql, params in session.statements if "INSERT INTO paper_chunks" in sql
        ]
        chunk_params = chunk_params_list[0]
        self.assertEqual(content_params["parse_status"], "ready")
        self.assertEqual(content_params["parser_name"], "structure_aware_v1")
        self.assertEqual(page_params["page_number"], 1)
        self.assertIn("Method paragraph", page_params["blocks"])
        self.assertEqual(section_params["section_id"], "method")
        self.assertEqual(chunk_params["section_id"], "method")
        self.assertEqual(chunk_params["section_path"], "2 Method")
        self.assertEqual(chunk_params["page_start"], 1)
        self.assertEqual(chunk_params["chunk_type"], "equation")
        self.assertEqual(chunk_params["parent_section_id"], "method")
        self.assertEqual(chunk_params["source_block_ids"], '["eq-1"]')
        self.assertEqual(chunk_params["context_before"], "Definitions before.")
        self.assertIn("Explanation after", chunk_params["embedding_content"])
        self.assertIn('"page_number": 1', chunk_params["chunk_metadata"])
        self.assertEqual(chunk_params_list[1]["chunk_type"], "code")
        self.assertEqual(chunk_params_list[1]["source_block_ids"], '["code-1"]')


if __name__ == "__main__":
    unittest.main()
