from __future__ import annotations

import unittest

from app.papers.object_quality import assess_object_quality


class ObjectQualityTest(unittest.TestCase):
    def test_invalid_tableformer_markdown_remains_review_without_invented_grid(self) -> None:
        """Catches the v4 quality path upgrading malformed source into fabricated cells."""
        markdown = "| Method | Score |\n| Scholar | 0.91 |"

        quality = assess_object_quality(
            "table",
            markdown,
            {"markdown": markdown, "source_image_available": True},
            (10.0, 20.0, 300.0, 180.0),
        )

        self.assertEqual(quality["status"], "review")
        self.assertIn("table_markdown_grid_missing", quality["reasons"])
        self.assertFalse(quality["checks"]["markdown_grid"])

    def test_complete_table_reports_row_column_integrity(self) -> None:
        markdown = "\n".join(
            (
                "| Method | Score |",
                "| --- | --- |",
                "| FedAvg | 82.1 |",
                "| RobustFL | 86.4 |",
            )
        )

        quality = assess_object_quality(
            "table",
            markdown,
            {"caption": "Table 1. Main results", "markdown": markdown},
            (10, 20, 500, 300),
        )

        self.assertEqual(quality["version"], "v1")
        self.assertEqual(quality["status"], "usable")
        self.assertEqual(quality["checks"]["row_count"], 2)
        self.assertEqual(quality["checks"]["column_count"], 2)
        self.assertTrue(quality["checks"]["columns_consistent"])

    def test_inconsistent_table_is_flagged_for_review(self) -> None:
        markdown = "\n".join(
            (
                "| Method | Score |",
                "| --- | --- |",
                "| FedAvg | 82.1 | extra |",
            )
        )

        quality = assess_object_quality(
            "table", markdown, {"markdown": markdown}, (10, 20, 500, 300)
        )

        self.assertEqual(quality["status"], "review")
        self.assertIn("table_columns_inconsistent", quality["reasons"])

    def test_escaped_table_pipe_does_not_create_a_phantom_column(self) -> None:
        markdown = "\n".join(
            (
                "| Method | Rule |",
                "| --- | --- |",
                "| FedAvg | A \\| B |",
            )
        )

        quality = assess_object_quality(
            "table",
            markdown,
            {"caption": "Table 1", "markdown": markdown},
            (10, 20, 500, 300),
        )

        self.assertEqual(quality["checks"]["column_count"], 2)
        self.assertTrue(quality["checks"]["columns_consistent"])

    def test_algorithm_reports_steps_and_io_contract(self) -> None:
        content = (
            "Input: client updates\nOutput: global model\n"
            "1. Initialize accumulator.\n2. Aggregate every update.\n3. Return model."
        )

        quality = assess_object_quality(
            "algorithm",
            content,
            {"caption": "Algorithm 1. Secure aggregation", "markdown": content},
            (10, 20, 500, 400),
        )

        self.assertEqual(quality["status"], "usable")
        self.assertEqual(quality["checks"]["step_count"], 3)
        self.assertTrue(quality["checks"]["has_input"])
        self.assertTrue(quality["checks"]["has_output"])

    def test_algorithm_io_without_steps_requires_review(self) -> None:
        content = "Input: client updates\nOutput: global model"

        quality = assess_object_quality(
            "algorithm",
            content,
            {"caption": "Algorithm 1", "markdown": content},
            (10, 20, 500, 200),
        )

        self.assertEqual(quality["status"], "review")
        self.assertEqual(quality["checks"]["step_count"], 0)
        self.assertIn("algorithm_steps_incomplete", quality["reasons"])

    def test_numbered_algorithm_io_is_not_counted_as_steps(self) -> None:
        content = "1. Input: client updates\n2. Output: global model"

        quality = assess_object_quality(
            "algorithm",
            content,
            {"caption": "Algorithm 1", "markdown": content},
            (10, 20, 500, 200),
        )

        self.assertEqual(quality["checks"]["step_count"], 0)
        self.assertIn("algorithm_steps_incomplete", quality["reasons"])

    def test_algorithm_counts_numbered_chinese_steps(self) -> None:
        content = "1. 初始化全局模型。\n2. 聚合客户端更新。"

        quality = assess_object_quality(
            "algorithm",
            content,
            {"caption": "算法 1", "markdown": content},
            (10, 20, 500, 200),
        )

        self.assertEqual(quality["checks"]["step_count"], 2)
        self.assertNotIn("algorithm_steps_incomplete", quality["reasons"])

    def test_low_confidence_equation_preserves_source_review_status(self) -> None:
        quality = assess_object_quality(
            "equation",
            "$$w=\\sum_i p_iw_i$$",
            {
                "markdown": "$$w=\\sum_i p_iw_i$$",
                "quality_status": "review",
                "extraction_confidence": 0.55,
                "source_image_available": True,
            },
            (10, 20, 500, 80),
        )

        self.assertEqual(quality["status"], "review")
        self.assertEqual(quality["score"], 0.55)
        self.assertIn("source_marked_review", quality["reasons"])
        self.assertTrue(quality["checks"]["source_image_available"])

    def test_equation_markdown_is_structured_without_duplicate_metadata(self) -> None:
        quality = assess_object_quality(
            "equation",
            "$$w=\\sum_i p_iw_i$$",
            {},
            (10, 20, 500, 80),
        )

        self.assertEqual(quality["status"], "usable")
        self.assertTrue(quality["checks"]["structured_content_available"])
        self.assertNotIn("equation_structured_text_missing", quality["reasons"])

    def test_equation_rejects_reversed_delimiter_order(self) -> None:
        quality = assess_object_quality(
            "equation",
            "$$}x+1{$$",
            {},
            (10, 20, 500, 80),
        )

        self.assertEqual(quality["status"], "review")
        self.assertFalse(quality["checks"]["delimiters_balanced"])
        self.assertIn("equation_delimiters_unbalanced", quality["reasons"])

    def test_source_quality_reasons_are_preserved_for_audit(self) -> None:
        quality = assess_object_quality(
            "figure",
            "Fig. 2. System model.",
            {
                "caption": "Fig. 2. System model.",
                "quality_status": "review",
                "quality_reasons": ["crop_contains_neighbor", "excess_whitespace"],
                "source_image_available": True,
            },
            (10, 20, 500, 300),
        )

        self.assertIn("crop_contains_neighbor", quality["reasons"])
        self.assertIn("excess_whitespace", quality["reasons"])

    def test_code_block_reports_fence_language_and_line_count(self) -> None:
        code = "```python\ndef aggregate(updates):\n    return sum(updates)\n```"

        quality = assess_object_quality(
            "code",
            code,
            {"markdown": code},
            (10, 20, 500, 180),
        )

        self.assertEqual(quality["status"], "usable")
        self.assertTrue(quality["checks"]["fenced_code"])
        self.assertEqual(quality["checks"]["language"], "python")
        self.assertEqual(quality["checks"]["line_count"], 2)

    def test_figure_without_caption_or_image_is_reviewable(self) -> None:
        quality = assess_object_quality(
            "figure",
            "",
            {
                "quality_status": "rejected",
                "quality_reasons": ["crop_render_failed"],
            },
            (0, 0, 0, 0),
        )

        self.assertEqual(quality["status"], "rejected")
        self.assertIn("empty_content", quality["reasons"])
        self.assertIn("source_marked_rejected", quality["reasons"])
        self.assertIn("crop_render_failed", quality["reasons"])

    def test_figure_caption_is_an_auditable_source_without_body_text(self) -> None:
        quality = assess_object_quality(
            "figure",
            "",
            {"caption": "Fig. 2. System model."},
            (10, 20, 500, 300),
        )

        self.assertEqual(quality["status"], "usable")
        self.assertTrue(quality["checks"]["caption_present"])
        self.assertFalse(quality["checks"]["content_present"])

    def test_figure_image_is_an_auditable_source_without_text(self) -> None:
        quality = assess_object_quality(
            "figure",
            "",
            {"asset_name": "page_002_figure_01.png"},
            (10, 20, 500, 300),
        )

        self.assertEqual(quality["status"], "usable")
        self.assertTrue(quality["checks"]["source_image_available"])
        self.assertFalse(quality["checks"]["content_present"])

    def test_image_only_table_is_reviewable_without_fabricating_a_markdown_grid(self) -> None:
        """Catches source-backed tables being rejected solely because TableFormer had no cells."""
        quality = assess_object_quality(
            "table",
            "",
            {
                "asset_name": "page_002_table_001.png",
                "source_image_available": True,
            },
            (10, 20, 500, 300),
        )

        self.assertEqual(quality["status"], "review")
        self.assertIn("table_markdown_grid_missing", quality["reasons"])
        self.assertNotIn("empty_content", quality["reasons"])
        self.assertTrue(quality["checks"]["source_image_available"])
        self.assertFalse(quality["checks"]["markdown_grid"])


if __name__ == "__main__":
    unittest.main()
