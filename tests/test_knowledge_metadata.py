from __future__ import annotations

import unittest

from app.routes.knowledge import PaperMetadataUpdateDTO, _merge_user_bibliography


class KnowledgeMetadataTest(unittest.TestCase):
    def test_merge_preserves_unchanged_evidence_and_marks_only_changes_as_user_edits(self) -> None:
        existing = {
            "title": {
                "value": "Original title",
                "source": "pdf_metadata.title",
                "confidence": 0.8,
                "user_edited": False,
            },
            "authors": {
                "value": ["Old Author"],
                "source": "pdf_metadata.author",
                "confidence": 0.65,
                "user_edited": False,
            },
        }
        request = PaperMetadataUpdateDTO(
            title=" Original title ",
            authors=["Alice", "Bob"],
            institutions=[],
            links={"code": ["https://github.com/acme/project"]},
        )

        merged = _merge_user_bibliography(existing, request)

        self.assertEqual(merged["title"], existing["title"])
        self.assertEqual(merged["authors"]["value"], ["Alice", "Bob"])
        self.assertEqual(merged["authors"]["source"], "user")
        self.assertEqual(merged["authors"]["confidence"], 1.0)
        self.assertTrue(merged["authors"]["user_edited"])
        self.assertEqual(
            merged["links"]["value"]["code"],
            ["https://github.com/acme/project"],
        )

    def test_metadata_update_contract_rejects_empty_title(self) -> None:
        with self.assertRaises(ValueError):
            PaperMetadataUpdateDTO(title="   ")


if __name__ == "__main__":
    unittest.main()
