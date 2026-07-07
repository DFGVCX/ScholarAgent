import unittest

from skills.survey_generation.tools.citation import CitationGuard


class CitationGuardTest(unittest.TestCase):
    def test_detects_hallucinated_source_ids(self):
        guard = CitationGuard()
        audit = guard.verify_citations(
            "Supported claim [paper:arxiv:2301.00001] unsupported [paper:fake:1].",
            [{"paper_id": "paper:arxiv:2301.00001"}],
        )

        self.assertFalse(audit["is_valid"])
        self.assertEqual(audit["hallucinated_ids"], ["paper:fake:1"])

    def test_passes_valid_source_ids(self):
        guard = CitationGuard()
        audit = guard.verify_citations(
            "Supported claim [paper:arxiv:2301.00001].",
            [{"paper_id": "paper:arxiv:2301.00001"}],
        )

        self.assertTrue(audit["is_valid"])
        self.assertEqual(audit["found_ids"], ["paper:arxiv:2301.00001"])


if __name__ == "__main__":
    unittest.main()

