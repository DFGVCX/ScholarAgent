from __future__ import annotations

from pathlib import Path
import importlib.util
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import app.papers.parsing as parsing


class _DoclingItem:
    def __init__(
        self,
        label: str,
        text: str,
        *,
        page: int = 1,
        bbox: tuple[float, float, float, float] = (10.0, 20.0, 300.0, 60.0),
        caption: str = "",
        markdown: str = "",
    ) -> None:
        self.label = label
        self.text = text
        self.prov = (SimpleNamespace(page_no=page, bbox=bbox),)
        self._caption = caption
        self._markdown = markdown

    def caption_text(self, _document) -> str:
        return self._caption

    def export_to_markdown(self, _document) -> str:
        return self._markdown or self.text


class _DoclingDocument:
    def __init__(self, items: list[tuple[_DoclingItem, int]]) -> None:
        self._items = items

    def iterate_items(self):
        return iter(self._items)


class _Converter:
    def __init__(self, document: _DoclingDocument) -> None:
        self.document = document

    def convert(self, _path: Path):
        return SimpleNamespace(document=self.document)


class HierarchicalPdfParsingTest(unittest.TestCase):
    def test_docling_output_is_normalized_without_leaking_docling_types(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("app.papers.docling_adapter"))
        from app.papers.docling_adapter import parse_docling_pdf

        document = _DoclingDocument(
            [
                (_DoclingItem("section_header", "2 Method"), 1),
                (_DoclingItem("text", "The server aggregates all client updates."), 2),
                (
                    _DoclingItem(
                        "formula",
                        "w^{t+1}=sum_i p_i w_i^t",
                        caption="Equation 1",
                        markdown="$$w^{t+1}=\\sum_i p_i w_i^t$$",
                    ),
                    2,
                ),
                (
                    _DoclingItem(
                        "table",
                        "Method Score FedAvg 82.4",
                        caption="Table 1. Main results",
                        markdown="| Method | Score |\n| --- | --- |\n| FedAvg | 82.4 |",
                    ),
                    2,
                ),
            ]
        )

        parsed = parse_docling_pdf(Path("paper.pdf"), converter=_Converter(document))

        self.assertEqual(parsed.status, "ready")
        self.assertEqual(parsed.manifest["parser"]["engine"], "docling")
        self.assertEqual(parsed.sections[0].title, "2 Method")
        typed = {block.block_type: block for block in parsed.pages[0].blocks}
        self.assertEqual(typed["equation"].metadata["markdown"], "$$w^{t+1}=\\sum_i p_i w_i^t$$")
        self.assertIn("| FedAvg | 82.4 |", typed["table"].metadata["markdown"])
        self.assertEqual(typed["table"].metadata["source_engine"], "docling")
        self.assertTrue(all(isinstance(block, parsing.ParsedBlock) for block in parsed.pages[0].blocks))

    def test_hierarchical_parser_falls_back_and_records_docling_failure(self) -> None:
        self.assertTrue(hasattr(parsing, "parse_pdf_hierarchical"))
        fallback = parsing.ParsedPaper(
            full_text="Fallback searchable paper text.",
            pages=(),
            sections=(),
            metadata={},
            manifest={"parser": {"name": parsing.MULTIMODAL_PARSER_NAME, "version": "3"}},
            status="ready",
            quality_score=0.8,
        )

        with (
            patch("app.papers.docling_adapter.parse_docling_pdf", side_effect=RuntimeError("model unavailable")),
            patch("app.papers.parsing.parse_pdf_multimodal", return_value=fallback),
        ):
            parsed = parsing.parse_pdf_hierarchical(Path("paper.pdf"))

        self.assertEqual(parsed.status, "ready")
        self.assertEqual(parsed.manifest["parser"]["name"], "scholar_hierarchical_v4")
        self.assertEqual(parsed.manifest["parser"]["engine"], "pymupdf_multimodal")
        self.assertEqual(parsed.manifest["fallback"]["from"], "docling")
        self.assertIn("model unavailable", parsed.manifest["fallback"]["reason"])
        self.assertIn("docling_fallback", parsed.warnings)

    def test_hierarchical_parser_contains_docling_system_exit(self) -> None:
        fallback = parsing.ParsedPaper(
            full_text="Fallback text remains available.",
            pages=(),
            sections=(),
            metadata={},
            manifest={"parser": {"name": parsing.MULTIMODAL_PARSER_NAME, "version": "3"}},
            status="ready",
            quality_score=0.8,
        )
        with (
            patch("app.papers.docling_adapter.parse_docling_pdf", side_effect=SystemExit(1)),
            patch("app.papers.parsing.parse_pdf_multimodal", return_value=fallback),
        ):
            try:
                parsed = parsing.parse_pdf_hierarchical(Path("paper.pdf"))
            except SystemExit:
                self.fail("a missing Docling model must not terminate the ingestion worker")

        self.assertEqual(parsed.status, "ready")
        self.assertEqual(parsed.manifest["fallback"]["reason"], "Docling exited with status 1")


if __name__ == "__main__":
    unittest.main()
