from __future__ import annotations

import unittest

from app.papers.formulas import (
    FormulaCandidate,
    contains_invalid_controls,
    extract_numbered_formula,
    recover_formula,
)


class FormulaParsingTest(unittest.TestCase):
    def test_detects_non_whitespace_c0_controls(self) -> None:
        self.assertTrue(contains_invalid_controls("E(x,y)\x02DL(x,w,y)"))
        self.assertFalse(contains_invalid_controls("line one\nline two\tvalue"))

    def test_extracts_numbered_multiline_formula_from_pypdf_page(self) -> None:
        page_text = """
received from n clients by Eq. 2,
wi =
∑ n
j=1 ζj
i w j
i , (2)
where ζj
i = |D j |
"""

        extracted = extract_numbered_formula(page_text, "2")

        self.assertEqual(extracted, "wi =\n∑ n\nj=1 ζj\ni w j\ni , (2)")

    def test_recovers_weighted_sum_as_renderable_markdown(self) -> None:
        candidate = recover_formula(
            raw_text="wi = \x03n j=1 ζ j i w j i, (2)",
            fallback_text="wi =\n∑ n\nj=1 ζj\ni w j\ni , (2)",
            label="2",
            page_number=3,
            bbox=(394.3, 406.4, 563.1, 431.8),
        )

        self.assertEqual(candidate.recovery_source, "pypdf_page_text")
        self.assertEqual(candidate.confidence, "high")
        self.assertIn(r"\sum_{j=1}^{n}", candidate.latex)
        self.assertIn(r"\zeta_j^i", candidate.latex)
        self.assertIn(r"w_j^i", candidate.latex)
        self.assertTrue(candidate.markdown.startswith("$$\n"))
        self.assertTrue(candidate.markdown.endswith("\n$$"))
        self.assertIn(r"\tag{2}", candidate.markdown)

    def test_equation_reference_line_is_not_absorbed_into_objective(self) -> None:
        page_text = """
the objective function described by
Eq. 1.
F(x,w, y) = min
w E(x,y)∼ ˜D L(x,w, y), (1)
where x is the training data
"""
        extracted = extract_numbered_formula(page_text, "1")
        candidate = recover_formula(
            raw_text="F(x,w,y) = min w E(x,y) D L(x,w,y), (1)",
            fallback_text=extracted,
            label="1",
            page_number=3,
            bbox=(1.0, 2.0, 3.0, 4.0),
        )

        self.assertNotIn("Eq. 1", candidate.latex)
        self.assertIn(r"\mathbb{E}", candidate.latex)
        self.assertIn(r"\min_{w}", candidate.latex)
        self.assertEqual(candidate.confidence, "high")

    def test_conservative_fallback_remains_visible_and_balanced(self) -> None:
        candidate = recover_formula(
            raw_text="score = x + y (7)",
            fallback_text="",
            label="7",
            page_number=4,
            bbox=(1.0, 2.0, 3.0, 4.0),
        )

        self.assertIsInstance(candidate, FormulaCandidate)
        self.assertEqual(candidate.recovery_source, "pymupdf")
        self.assertEqual(candidate.confidence, "medium")
        self.assertNotIn("\x00", candidate.markdown)
        self.assertEqual(candidate.markdown.count("$$"), 2)

    def test_worse_pypdf_fragment_does_not_replace_more_complete_layout_text(self) -> None:
        candidate = recover_formula(
            raw_text="g j\ni = g j\ni / ||g j\ni||, (6)",
            fallback_text="i, (6)",
            label="6",
            page_number=7,
            bbox=(1.0, 2.0, 3.0, 4.0),
        )

        self.assertEqual(candidate.recovery_source, "pymupdf")
        self.assertIn("=", candidate.latex)
        self.assertGreater(len(candidate.latex), 10)

    def test_layout_text_after_equation_label_is_not_absorbed(self) -> None:
        candidate = recover_formula(
            raw_text="x = 1 (3)\ny = 2 starts the next line",
            fallback_text="",
            label="3",
            page_number=5,
            bbox=(1.0, 2.0, 3.0, 4.0),
        )

        self.assertIn("x = 1", candidate.latex)
        self.assertNotIn("y = 2", candidate.latex)


if __name__ == "__main__":
    unittest.main()
