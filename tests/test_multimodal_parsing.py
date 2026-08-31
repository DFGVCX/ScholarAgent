from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import fitz

from app.papers.parsing import (
    MULTIMODAL_PARSER_NAME,
    ParsedBlock,
    _matching_equation_record,
    parse_pdf_multimodal,
)
from app.papers.visuals import (
    _table_candidates,
    _table_matches_caption,
    caption_kind,
    rows_to_markdown,
)


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
        page.insert_text(
            (95, 520),
            "This long explanatory paragraph resumes the paper prose after the algorithm and must not be parsed as pseudocode.",
            fontsize=10,
        )
        document.save(path)
    finally:
        document.close()
    return path


def _write_two_column_visual_pdf(path: Path) -> Path:
    document = fitz.open()
    try:
        page = document.new_page(width=612, height=792)
        page.insert_text((45, 28), "SHARED JOURNAL HEADER", fontsize=9)
        page.draw_rect(fitz.Rect(45, 70, 285, 180), color=(0, 0, 0))
        page.insert_text((110, 195), "Table I. Left-column comparison.", fontsize=9)
        page.draw_rect(fitz.Rect(335, 85, 575, 205), color=(0.1, 0.3, 0.6), fill=(0.9, 0.95, 1.0))
        page.insert_text((385, 222), "Fig. 1. Right-column architecture.", fontsize=9)
        page.insert_text((45, 260), "The following searchable prose keeps the synthetic document usable for parsing tests.", fontsize=10)
        document.save(path)
    finally:
        document.close()
    return path


def _write_stacked_figures_pdf(path: Path) -> Path:
    document = fitz.open()
    try:
        page = document.new_page(width=612, height=792)
        page.insert_text((45, 45), "Results", fontsize=14)
        page.draw_rect(fitz.Rect(45, 85, 285, 175), color=(0.2, 0.2, 0.2), fill=(0.95, 0.95, 0.95))
        page.insert_text((100, 190), "Fig. 3. Earlier result.", fontsize=9)
        page.draw_rect(fitz.Rect(45, 230, 285, 330), color=(0.1, 0.3, 0.6), fill=(0.9, 0.95, 1.0))
        page.insert_text((100, 345), "Fig. 4. Later result.", fontsize=9)
        page.insert_text((45, 390), "A complete searchable explanation follows both figures in this synthetic paper page.", fontsize=10)
        document.save(path)
    finally:
        document.close()
    return path


class MultimodalPdfParsingTest(unittest.TestCase):
    def test_matches_repeated_equation_labels_by_source_bbox(self) -> None:
        records = [
            {"label": "1", "bbox": [10.0, 20.0, 100.0, 40.0]},
            {"label": "1", "bbox": [10.0, 220.0, 100.0, 240.0]},
        ]
        later = ParsedBlock(
            1,
            "equation",
            "second equation",
            (10.0, 220.0, 100.0, 240.0),
            5,
            metadata=dict(records[1]),
        )

        matched = _matching_equation_record(records, later)

        self.assertIs(matched, records[1])

    def test_classifies_visual_caption_labels(self) -> None:
        self.assertEqual(caption_kind("Figure 2. System overview"), "figure")
        self.assertEqual(caption_kind("Table 3: Main results"), "table")
        self.assertEqual(caption_kind("TABLE IV. Computation overhead"), "table")
        self.assertEqual(caption_kind("TABLE I"), "table")
        self.assertEqual(caption_kind("Algorithm 1 Aggregate updates"), "algorithm")
        self.assertEqual(caption_kind("Scheme 4. Training procedure"), "figure")
        self.assertIsNone(caption_kind("Table 2 shows the results in prose."))

    def test_table_detection_falls_back_to_text_alignment(self) -> None:
        table = SimpleNamespace(
            bbox=(10.0, 20.0, 300.0, 180.0),
            extract=lambda: [["Method", "Score"], ["PBFL", "86.1"]],
        )

        class FakePage:
            rect = SimpleNamespace(x0=0.0, y0=0.0, x1=595.0, y1=842.0)

            def __init__(self) -> None:
                self.calls = []

            def find_tables(self, **kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(tables=[] if not kwargs else [table])

        page = FakePage()
        candidates = _table_candidates(page)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["rows"][1][0], "PBFL")
        self.assertEqual(
            page.calls,
            [{}, {"vertical_strategy": "text", "horizontal_strategy": "text"}],
        )

    def test_table_detection_rejects_page_wide_text_grid(self) -> None:
        table = SimpleNamespace(
            bbox=(0.0, 0.0, 595.0, 842.0),
            extract=lambda: [["whole", "page"]],
        )

        class FakePage:
            rect = SimpleNamespace(x0=0.0, y0=0.0, x1=595.0, y1=842.0)

            def find_tables(self, **kwargs):
                return SimpleNamespace(tables=[] if not kwargs else [table])

        self.assertEqual(_table_candidates(FakePage()), [])

    def test_table_candidate_must_share_the_caption_column(self) -> None:
        page_rect = SimpleNamespace(x0=0.0, y0=0.0, x1=612.0, y1=792.0)
        left_caption = (150.0, 58.0, 190.0, 67.0)

        self.assertFalse(
            _table_matches_caption(
                page_rect,
                {"bbox": (228.0, 0.0, 596.0, 303.0)},
                left_caption,
            )
        )
        self.assertTrue(
            _table_matches_caption(
                page_rect,
                {"bbox": (49.0, 98.0, 299.0, 207.0)},
                left_caption,
            )
        )

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
                self.assertIsInstance(typed[kind].metadata["extraction_confidence"], float)
                self.assertTrue(typed[kind].metadata["source_image_available"])
                self.assertIn(
                    typed[kind].metadata["fallback_mode"],
                    {"none", "source_image", "caption_only"},
                )
            self.assertFalse(typed["figure"].metadata["structured_content_available"])
            self.assertTrue(typed["algorithm"].metadata["structured_content_available"])
            self.assertIn("visual_blocks", parsed.manifest)
            self.assertEqual(len(parsed.manifest["visual_blocks"]), 2)
            inventory = parsed.to_manifest()["asset_inventory"]
            self.assertEqual(
                {item["name"] for item in inventory},
                {typed["figure"].metadata["asset_name"], typed["algorithm"].metadata["asset_name"]},
            )
            self.assertEqual({item["type"] for item in inventory}, {"figure", "algorithm"})
            self.assertTrue(all(item["page_number"] == 1 for item in inventory))
            algorithm = typed["algorithm"]
            self.assertNotIn("resumes the paper prose", algorithm.text)
            self.assertNotIn("resumes the paper prose", algorithm.metadata["markdown"])
            self.assertLess(algorithm.bbox[3], 520.0)

    def test_figure_crop_stays_in_caption_column_and_below_header(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write_two_column_visual_pdf(Path(tmp) / "columns.pdf")
            parsed = parse_pdf_multimodal(path)

        figure = next(
            block
            for page in parsed.pages
            for block in page.blocks
            if block.block_type == "figure"
        )
        self.assertGreaterEqual(figure.bbox[0], 300.0)
        self.assertGreater(figure.bbox[1], 40.0)

    def test_later_figure_crop_does_not_include_previous_figure(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write_stacked_figures_pdf(Path(tmp) / "stacked.pdf")
            parsed = parse_pdf_multimodal(path)

        figures = sorted(
            (
                block
                for page in parsed.pages
                for block in page.blocks
                if block.block_type == "figure"
            ),
            key=lambda block: block.metadata["label"],
        )
        self.assertEqual(len(figures), 2)
        self.assertGreater(figures[1].bbox[1], figures[0].bbox[3])


if __name__ == "__main__":
    unittest.main()
