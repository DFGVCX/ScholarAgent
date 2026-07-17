from __future__ import annotations

from pathlib import Path
import unittest

import fitz

from app.papers.parsing import parse_pdf, parse_pdf_legacy


def _write_text_pdf(path: Path, pages: list[list[str]]) -> Path:
    document = fitz.open()
    try:
        for lines in pages:
            page = document.new_page(width=595, height=842)
            for index, line in enumerate(lines):
                if index == 0:
                    y = 32
                    size = 9
                elif index == len(lines) - 1 and line.isdigit():
                    y = 820
                    size = 9
                else:
                    y = 110 + (index - 1) * 72
                    size = 15 if line.lower().lstrip("0123456789. ") in {
                        "abstract", "introduction", "method"
                    } else 11
                page.insert_text((72, y), line, fontsize=size)
        document.set_metadata({"title": "A Structured Federated Learning Paper"})
        document.save(path)
    finally:
        document.close()
    return path


def _write_image_only_pdf(path: Path) -> Path:
    document = fitz.open()
    try:
        page = document.new_page(width=595, height=842)
        page.draw_rect(fitz.Rect(80, 100, 500, 700), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        document.save(path)
    finally:
        document.close()
    return path


class StructuredPdfParsingTest(unittest.TestCase):
    def test_preserves_pages_sections_and_provenance(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            path = _write_text_pdf(
                Path(tmp) / "paper.pdf",
                [
                    [
                        "Shared Conference Header",
                        "Abstract",
                        "This paper studies privacy-preserving federated learning across distributed clients.",
                        "doi:10.1000/Scholar.1234",
                        "1",
                    ],
                    [
                        "Shared Conference Header",
                        "1 Introduction",
                        "Federated learning coordinates model training without collecting every private dataset centrally.",
                        "The complete introduction remains attached to its source page for reliable retrieval debugging.",
                        "2",
                    ],
                    [
                        "Shared Conference Header",
                        "2 Method",
                        "Our training method aggregates protected local updates and records reproducible evaluation details.",
                        "Code is available at https://github.com/example/federated-paper for comparison experiments.",
                        "3",
                    ],
                ],
            )

            parsed = parse_pdf(path)

        self.assertEqual(parsed.status, "ready")
        self.assertEqual(len(parsed.pages), 3)
        self.assertEqual(
            [section.kind for section in parsed.sections],
            ["abstract", "introduction", "method"],
        )
        self.assertEqual(parsed.sections[-1].page_start, 3)
        self.assertEqual(parsed.sections[-1].page_end, 3)
        self.assertNotIn("Shared Conference Header", parsed.full_text)
        self.assertEqual(parsed.manifest["coverage"]["pages_extracted"], 3)
        self.assertEqual(parsed.metadata["doi"], "10.1000/scholar.1234")
        self.assertEqual(parsed.metadata["code_urls"], ["https://github.com/example/federated-paper"])
        self.assertTrue(parsed.sections[1].text_hash)
        self.assertLess(parsed.sections[1].char_start, parsed.sections[1].char_end)

    def test_marks_image_only_document_as_needs_ocr(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            parsed = parse_pdf(_write_image_only_pdf(Path(tmp) / "scan.pdf"))

        self.assertEqual(parsed.status, "needs_ocr")
        self.assertEqual(parsed.full_text, "")
        self.assertIn("searchable_text_insufficient", parsed.warnings)

    def test_failure_is_not_silent(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.pdf"
            path.write_bytes(b"not a pdf")
            parsed = parse_pdf(path)

        self.assertEqual(parsed.status, "failed")
        self.assertTrue(parsed.error)
        self.assertEqual(parsed.full_text, "")

    def test_legacy_parser_remains_available(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            path = _write_text_pdf(
                Path(tmp) / "legacy.pdf",
                [["Header", "A complete legacy extraction paragraph with enough searchable text.", "1"]],
            )
            parsed = parse_pdf_legacy(path)

        self.assertEqual(parsed.manifest["parser"]["name"], "legacy_fixed")
        self.assertIn("complete legacy extraction", parsed.full_text)


if __name__ == "__main__":
    unittest.main()
