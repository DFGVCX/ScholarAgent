from __future__ import annotations

import unittest

from app.papers.metadata import build_bibliography
from app.papers.models import PaperInput


class PaperMetadataTest(unittest.TestCase):
    def test_pdf_title_replaces_filename_stem_but_not_an_explicit_title(self) -> None:
        parsed = {"pdf_metadata": {"title": "A Reliable Federated Learning Method"}}
        uploaded = PaperInput(
            paper_id="paper-1",
            source="pdf",
            title="2401.12345",
            file_name="2401.12345.pdf",
        )
        explicit = PaperInput(
            paper_id="paper-2",
            source="pdf",
            title="Author supplied title",
            file_name="2401.12345.pdf",
        )

        uploaded_bibliography = build_bibliography(uploaded, parsed, "Abstract\nText")
        explicit_bibliography = build_bibliography(explicit, parsed, "Abstract\nText")

        self.assertEqual(
            uploaded_bibliography["title"],
            {
                "value": "A Reliable Federated Learning Method",
                "source": "pdf_metadata.title",
                "confidence": 0.8,
                "user_edited": False,
            },
        )
        self.assertEqual(explicit_bibliography["title"]["value"], "Author supplied title")
        self.assertEqual(explicit_bibliography["title"]["source"], "ingest_input")

    def test_bibliography_has_complete_auditable_field_contract(self) -> None:
        paper = PaperInput(
            paper_id="paper-1",
            source="arxiv",
            title="Federated Learning Systems: A Survey",
            authors=("Alice Smith", "Bob Zhang"),
            published_at="2024-05-01",
            arxiv_id="2405.00001",
            metadata={"venue": "arXiv"},
        )
        parsed_metadata = {
            "doi": "10.1000/example",
            "code_urls": ["https://github.com/example/fed-system"],
            "pdf_metadata": {"author": "Alice Smith; Bob Zhang"},
        }
        full_text = (
            "Federated Learning Systems: A Survey\n"
            "Alice Smith; Bob Zhang\n"
            "Department of Computer Science, Example University\n"
            "Abstract\nThis paper reviews federated learning."
        )

        bibliography = build_bibliography(paper, parsed_metadata, full_text)

        self.assertEqual(
            set(bibliography),
            {
                "title",
                "title_translation",
                "authors",
                "institutions",
                "published_at",
                "venue",
                "doi",
                "arxiv_id",
                "links",
                "paper_type",
            },
        )
        self.assertEqual(bibliography["title"]["value"], paper.title)
        self.assertEqual(bibliography["authors"]["value"], list(paper.authors))
        self.assertEqual(
            bibliography["institutions"]["value"],
            ["Department of Computer Science, Example University"],
        )
        self.assertEqual(bibliography["doi"]["value"], "10.1000/example")
        self.assertEqual(
            bibliography["links"]["value"]["code"],
            ["https://github.com/example/fed-system"],
        )
        self.assertEqual(bibliography["paper_type"]["value"], "review")
        self.assertEqual(bibliography["title_translation"]["value"], "")
        for field in bibliography.values():
            self.assertIn("source", field)
            self.assertIn("confidence", field)
            self.assertFalse(field["user_edited"])

    def test_user_edited_field_survives_reparse(self) -> None:
        edited = {
            "value": "人工修正标题",
            "source": "user",
            "confidence": 1.0,
            "user_edited": True,
        }
        paper = PaperInput(
            paper_id="paper-1",
            source="upload",
            title="Parser title",
            metadata={"bibliography": {"title_translation": edited}},
        )

        bibliography = build_bibliography(
            paper,
            {"pdf_metadata": {"title": "PDF title"}},
            "PDF title\nAbstract\nText",
        )

        self.assertEqual(bibliography["title_translation"], edited)

    def test_reference_affiliations_are_not_scanned_after_abstract(self) -> None:
        paper = PaperInput(
            paper_id="paper-1",
            source="upload",
            title="A Method",
        )
        text = (
            "A Method\nAlice Smith\nAbstract\nMain text\nReferences\n"
            "Other Author, Example University"
        )

        bibliography = build_bibliography(paper, {}, text)

        self.assertEqual(bibliography["institutions"]["value"], [])
        self.assertEqual(bibliography["paper_type"]["value"], "")
        self.assertEqual(bibliography["paper_type"]["source"], "not_found")
        self.assertEqual(bibliography["paper_type"]["confidence"], 0.0)


if __name__ == "__main__":
    unittest.main()
