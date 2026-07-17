from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.papers.ingestion import PaperIngestionService
from app.papers.models import ContentVersion, PaperInput, PaperRecord
from app.papers.parsing import ParsedBlock, ParsedPage, ParsedPaper, ParsedSection
from app.retrieval.embedding import EmbeddingUnavailable


def _record() -> PaperRecord:
    now = datetime.now(timezone.utc)
    return PaperRecord(
        paper_uuid=uuid4(), tenant_id="t", user_id="u", paper_id="paper-1",
        source="manual", title="Paper", authors=(), abstract="", published_at=None,
        doi=None, arxiv_id=None, url=None, in_knowledge_base=True,
        ingestion_status="embedding", current_content_version=1, metadata={},
        created_at=now, updated_at=now,
    )


class _Repository:
    def __init__(self, record: PaperRecord) -> None:
        self.record = record
        self.failed = None
        self.vectors = None
        self.embedding_model = None
        self.saved_paper = None
        self.saved_chunks = []
        self.replace_kwargs = {}
        self.parsing_failure = None

    async def save(self, *args):
        self.saved_paper = args[-1]
        return self.record

    async def save_asset(self, *args):
        return None

    async def replace_content(self, *args, **kwargs):
        self.saved_chunks = list(args[5])
        self.replace_kwargs = kwargs
        return ContentVersion(self.record.paper_uuid, uuid4(), 1, len(args[5]))

    async def set_embeddings(self, *args, **kwargs):
        self.vectors = args[4]
        self.embedding_model = kwargs["model"]

    async def mark_embedding_failed(self, *args):
        self.failed = args[-1]

    async def mark_parsing_failed(self, *args):
        self.parsing_failure = args[-1]

    async def get(self, *args):
        return self.record


class _Embedding:
    model = "Qwen3-Embedding-4B"

    def __init__(self) -> None:
        self.texts = []

    async def embed(self, texts):
        self.texts = list(texts)
        return [[1.0] + [0.0] * 1023 for _ in texts]


class _BrokenEmbedding:
    async def embed(self, texts):
        raise EmbeddingUnavailable("qwen offline")


class PaperIngestionTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, embedding):
        repository = _Repository(_record())

        @asynccontextmanager
        async def transaction(*_):
            yield object()

        service = PaperIngestionService(
            embedding,
            transaction_factory=transaction,
            repository_factory=lambda _: repository,
        )
        paper = PaperInput(
            paper_id="paper-1", source="manual", title="Paper",
            full_text="A paragraph with enough text to index for retrieval.",
        )
        with patch(
            "app.papers.ingestion.get_settings",
            return_value=SimpleNamespace(
                rag_chunk_size=900,
                rag_chunk_overlap=120,
                rag_chunk_strategy="structure_aware_v1",
                pdf_parse_strategy="structure_aware_v1",
            ),
        ):
            result = await service.ingest("t", "u", paper)
        return result, repository

    async def test_successful_ingestion_writes_embeddings(self) -> None:
        result, repository = await self._run(_Embedding())
        self.assertEqual(result.embedding_status, "ready")
        self.assertEqual(len(repository.vectors), 1)
        self.assertEqual(repository.embedding_model, "Qwen3-Embedding-4B")

    async def test_embedding_failure_preserves_lexical_content(self) -> None:
        result, repository = await self._run(_BrokenEmbedding())
        self.assertEqual(result.embedding_status, "failed")
        self.assertIn("offline", repository.failed)
        self.assertEqual(result.chunk_count, 1)

    async def test_pdf_uses_structured_parser_and_contextualized_embedding(self) -> None:
        repository = _Repository(_record())
        embedding = _Embedding()
        parsed = _parsed("ready")

        @asynccontextmanager
        async def transaction(*_):
            yield object()

        service = PaperIngestionService(
            embedding,
            parser=lambda _: parsed,
            transaction_factory=transaction,
            repository_factory=lambda _: repository,
        )
        paper = PaperInput(
            paper_id="paper-1",
            source="pdf",
            title="Paper",
            file_uri=str(Path("paper.pdf")),
            file_name="paper.pdf",
            mime_type="application/pdf",
            file_sha256="a" * 64,
            file_size=123,
        )
        with patch(
            "app.papers.ingestion.get_settings",
            return_value=SimpleNamespace(
                rag_chunk_size=900,
                rag_chunk_overlap=120,
                rag_chunk_strategy="structure_aware_v1",
                pdf_parse_strategy="structure_aware_v1",
            ),
        ):
            result = await service.ingest("t", "u", paper)

        self.assertEqual(result.parse_status, "ready")
        self.assertEqual(repository.saved_chunks[0].content, "Raw PDF paragraph for retrieval.")
        self.assertEqual(repository.saved_chunks[0].section_id, "method")
        self.assertTrue(embedding.texts[0].startswith("Paper: Paper\nSection: 2 Method\n\n"))
        self.assertEqual(repository.replace_kwargs["parser_name"], "structure_aware_v1")

    async def test_needs_ocr_pdf_is_not_embedded(self) -> None:
        repository = _Repository(_record())
        embedding = _Embedding()

        @asynccontextmanager
        async def transaction(*_):
            yield object()

        service = PaperIngestionService(
            embedding,
            parser=lambda _: _parsed("needs_ocr"),
            transaction_factory=transaction,
            repository_factory=lambda _: repository,
        )
        paper = PaperInput(
            paper_id="paper-1",
            source="pdf",
            title="Paper",
            file_uri="scan.pdf",
            file_name="scan.pdf",
            mime_type="application/pdf",
            file_sha256="b" * 64,
            file_size=321,
        )
        with patch(
            "app.papers.ingestion.get_settings",
            return_value=SimpleNamespace(
                rag_chunk_size=900,
                rag_chunk_overlap=120,
                rag_chunk_strategy="structure_aware_v1",
                pdf_parse_strategy="structure_aware_v1",
            ),
        ):
            result = await service.ingest("t", "u", paper)

        self.assertEqual(result.parse_status, "needs_ocr")
        self.assertEqual(result.embedding_status, "not_indexed")
        self.assertEqual(embedding.texts, [])
        self.assertIn("searchable_text_insufficient", repository.parsing_failure)

    async def test_manual_edit_does_not_reparse_attached_pdf(self) -> None:
        repository = _Repository(_record())

        def parser(_: Path) -> ParsedPaper:
            raise AssertionError("manual edit must not reparse the PDF")

        @asynccontextmanager
        async def transaction(*_):
            yield object()

        service = PaperIngestionService(
            _Embedding(),
            parser=parser,
            transaction_factory=transaction,
            repository_factory=lambda _: repository,
        )
        paper = PaperInput(
            paper_id="paper-1",
            source="pdf",
            title="Paper",
            full_text="Manually corrected complete paper text.",
            file_uri="paper.pdf",
            file_name="paper.pdf",
            mime_type="application/pdf",
            file_sha256="c" * 64,
            file_size=222,
            metadata={"updated_from": "inline_file_editor"},
        )
        with patch(
            "app.papers.ingestion.get_settings",
            return_value=SimpleNamespace(
                rag_chunk_size=900,
                rag_chunk_overlap=120,
                rag_chunk_strategy="structure_aware_v1",
                pdf_parse_strategy="structure_aware_v1",
            ),
        ):
            result = await service.ingest("t", "u", paper)

        self.assertEqual(result.parse_status, "manual")
        self.assertEqual(repository.saved_paper.full_text, paper.full_text)


def _parsed(status: str) -> ParsedPaper:
    if status != "ready":
        return ParsedPaper(
            full_text="",
            pages=(),
            sections=(),
            metadata={},
            manifest={"parser": {"name": "structure_aware_v1", "version": "1"}},
            status=status,
            quality_score=0.0,
            warnings=("searchable_text_insufficient",),
        )
    text = "Raw PDF paragraph for retrieval."
    block = ParsedBlock(1, "body", text, (1.0, 2.0, 3.0, 4.0), 0, 11.0)
    page = ParsedPage(1, text, "page-hash", len(text), "pymupdf_layout", "usable", (block,))
    section = ParsedSection("method", 0, "method", "2 Method", 1, 1, text, 0, len(text), "hash")
    return ParsedPaper(
        full_text=f"2 Method\n\n{text}",
        pages=(page,),
        sections=(section,),
        metadata={"language": "en"},
        manifest={"parser": {"name": "structure_aware_v1", "version": "1"}},
        status="ready",
        quality_score=0.9,
    )


if __name__ == "__main__":
    unittest.main()
