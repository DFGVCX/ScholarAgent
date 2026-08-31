from __future__ import annotations

import unittest

from app.papers.parsing import ParsedBlock, _formula_aware_blocks
from app.papers.formulas import (
    FormulaCandidate,
    contains_invalid_controls,
    extract_numbered_formula,
    recover_formula,
)


class FormulaParsingTest(unittest.TestCase):
    def test_big_o_complexity_is_not_misclassified_as_equation_number(self) -> None:
        block = ParsedBlock(
            1,
            "body",
            "runtime = O(1)",
            (20.0, 30.0, 260.0, 45.0),
            0,
            10.0,
        )

        parsed_blocks, equations = _formula_aware_blocks((block,), block.text)

        self.assertEqual(parsed_blocks, (block,))
        self.assertEqual(equations, [])

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

    def test_extracts_requested_occurrence_when_equation_label_repeats(self) -> None:
        page_text = """
first = n + 1, (1)
The paragraph between equations is not formula text.
second = sqrt(n), (1)
"""

        first = extract_numbered_formula(page_text, "1", occurrence=0)
        second = extract_numbered_formula(page_text, "1", occurrence=1)

        self.assertEqual(first, "first = n + 1, (1)")
        self.assertEqual(second, "second = sqrt(n), (1)")

    def test_pypdf_extractor_ignores_big_o_suffix(self) -> None:
        self.assertEqual(extract_numbered_formula("runtime = O(1)", "1"), "")

    def test_recovery_keeps_big_o_term_before_real_equation_label(self) -> None:
        candidate = recover_formula(
            raw_text="cost = O(1), (1)",
            fallback_text="",
            label="1",
            page_number=1,
            bbox=(1.0, 2.0, 3.0, 4.0),
        )

        self.assertIn("O(1)", candidate.latex)

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

    def test_normalizes_common_unicode_math_operators_to_latex(self) -> None:
        candidate = recover_formula(
            raw_text="g = ∇L · x ⊙ y / ‖x‖ + ‖y‖ + a ∗ b × c → d (9)",
            fallback_text="",
            label="9",
            page_number=5,
            bbox=(1.0, 2.0, 3.0, 4.0),
        )

        for command in (
            r"\nabla",
            r"\cdot",
            r"\odot",
            r"\lVert",
            r"\rVert",
            r"\times",
            r"\to",
        ):
            self.assertIn(command, candidate.latex)
        for raw_symbol in ("∇", "·", "⊙", "‖", "∥", "∗", "×", "→"):
            self.assertNotIn(raw_symbol, candidate.latex)


if __name__ == "__main__":
    unittest.main()
