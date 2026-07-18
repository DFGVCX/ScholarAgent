from __future__ import annotations

import unittest
from uuid import UUID

from app.papers.chunking import ChunkDraft
from app.papers.models import PaperInput, normalize_arxiv_id, normalize_doi
from app.papers.parsing import ParsedBlock, ParsedPage, ParsedPaper, ParsedSection
from app.papers.repository import PaperRepository


class _Mappings:
    def first(self):
        return None


class _Result:
    def mappings(self):
        return _Mappings()


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


class _WriteSession(_Session):
    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, params or {}))
        if "SELECT current_content_version" in sql:
            return _WriteResult({"current_content_version": 0})
        if "INSERT INTO paper_contents" in sql:
            return _WriteResult(scalar=UUID("00000000-0000-0000-0000-000000000222"))
        return _WriteResult()


class PaperRepositoryTest(unittest.IsolatedAsyncioTestCase):
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
        )

        version = await PaperRepository(session).replace_content(
            "tenant-a",
            "user-a",
            UUID("00000000-0000-0000-0000-000000000111"),
            parsed.full_text,
            "content-hash",
            [chunk],
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
        chunk_params = next(params for sql, params in session.statements if "INSERT INTO paper_chunks" in sql)
        self.assertEqual(content_params["parse_status"], "ready")
        self.assertEqual(content_params["parser_name"], "structure_aware_v1")
        self.assertEqual(page_params["page_number"], 1)
        self.assertIn("Method paragraph", page_params["blocks"])
        self.assertEqual(section_params["section_id"], "method")
        self.assertEqual(chunk_params["section_id"], "method")
        self.assertEqual(chunk_params["section_path"], "2 Method")
        self.assertEqual(chunk_params["page_start"], 1)


if __name__ == "__main__":
    unittest.main()
