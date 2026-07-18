from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import fitz

from app.papers.parsing import (
    MULTIMODAL_PARSER_NAME,
    ParsedBlock,
    parse_pdf_multimodal,
)
from app.papers.visuals import caption_kind, rows_to_markdown


def _write_visual_pdf(path: Path) -> Path:
    document = fitz.open()
    try:
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 70), "Method", fontsize=15)
        page.insert_text(
            (72, 105),
            "The complete method explanation keeps this synthetic page searchable for tests.",
            fontsize=11,
        )
        page.draw_rect(fitz.Rect(95, 150, 500, 300), color=(0.1, 0.3, 0.6), fill=(0.9, 0.95, 1.0))
        page.draw_line((120, 225), (470, 225), color=(0.1, 0.3, 0.6), width=3)
        page.insert_text((95, 325), "Figure 1. Federated learning architecture overview.", fontsize=10)

        page.insert_text((95, 390), "Algorithm 1. Aggregate protected client updates.", fontsize=10)
        page.insert_text((115, 420), "Input: client updates and aggregation weights", fontsize=10)
        page.insert_text((115, 445), "for each client update do", fontsize=10)
        page.insert_text((135, 470), "add the weighted update to the global model", fontsize=10)
        page.insert_text((115, 495), "Output: next global model", fontsize=10)
        document.save(path)
    finally:
        document.close()
    return path


class MultimodalPdfParsingTest(unittest.TestCase):
    def test_classifies_visual_caption_labels(self) -> None:
        self.assertEqual(caption_kind("Figure 2. System overview"), "figure")
        self.assertEqual(caption_kind("Table 3: Main results"), "table")
        self.assertEqual(caption_kind("Algorithm 1 Aggregate updates"), "algorithm")
        self.assertEqual(caption_kind("Scheme 4. Training procedure"), "figure")
        self.assertIsNone(caption_kind("Table 2 shows the results in prose."))

    def test_converts_extracted_cells_to_renderable_markdown(self) -> None:
        markdown = rows_to_markdown(
            [
                ["Method", "Accuracy", "Notes"],
                ["FedAvg", "82.4", "baseline"],
                ["Ours", "86.1", "uses | protected updates"],
            ]
        )

        self.assertEqual(markdown.count("\n"), 3)
        self.assertIn("| Method | Accuracy | Notes |", markdown)
        self.assertIn("| --- | --- | --- |", markdown)
        self.assertIn(r"uses \| protected updates", markdown)

    def test_serializes_typed_block_metadata(self) -> None:
        block = ParsedBlock(
            2,
            "table",
            "Table 1. Main comparison",
            (10.0, 20.0, 300.0, 180.0),
            3,
            10.0,
            metadata={"label": "Table 1", "markdown": "| A | B |"},
        )

        payload = block.to_dict()

        self.assertEqual(payload["block_type"], "table")
        self.assertEqual(payload["metadata"]["label"], "Table 1")
        self.assertEqual(payload["metadata"]["markdown"], "| A | B |")

    def test_multimodal_parser_keeps_visual_blocks_and_assets(self) -> None:
        with TemporaryDirectory() as tmp:
            pdf_path = _write_visual_pdf(Path(tmp) / "visual.pdf")
            parsed = parse_pdf_multimodal(pdf_path)
            blocks = [block for page in parsed.pages for block in page.blocks]
            typed = {block.block_type: block for block in blocks if block.block_type != "body"}

            self.assertEqual(parsed.status, "ready")
            self.assertEqual(parsed.manifest["parser"]["name"], MULTIMODAL_PARSER_NAME)
            self.assertIn("figure", typed)
            self.assertIn("algorithm", typed)
            for kind in ("figure", "algorithm"):
                asset_name = typed[kind].metadata["asset_name"]
                self.assertTrue(asset_name.endswith(".png"))
                self.assertTrue((pdf_path.parent / "visual_assets" / asset_name).is_file())
                self.assertIn(typed[kind].metadata["quality_status"], {"usable", "review"})
            self.assertIn("visual_blocks", parsed.manifest)
            self.assertEqual(len(parsed.manifest["visual_blocks"]), 2)


if __name__ == "__main__":
    unittest.main()
