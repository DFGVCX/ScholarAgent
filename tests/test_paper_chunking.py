from __future__ import annotations

import unittest

from app.papers.chunking import chunk_sections, chunk_text
from app.papers.parsing import ParsedSection


def _section(
    section_id: str,
    title: str,
    text: str,
    *,
    page_start: int = 1,
    page_end: int = 1,
    kind: str = "introduction",
) -> ParsedSection:
    return ParsedSection(
        section_id=section_id,
        index=0,
        kind=kind,
        title=title,
        page_start=page_start,
        page_end=page_end,
        text=text,
        char_start=0,
        char_end=len(text),
        text_hash="hash",
    )


class PaperChunkingTest(unittest.TestCase):
    def test_chunks_are_stable_nonempty_and_ordered(self) -> None:
        text = "First paragraph explains retrieval.\n\nSecond paragraph explains storage consistency."
        first = chunk_text(text, max_chars=45, overlap_chars=8)
        second = chunk_text(text, max_chars=45, overlap_chars=8)

        self.assertEqual(first, second)
        self.assertEqual([chunk.position for chunk in first], list(range(len(first))))
        self.assertTrue(all(chunk.content.strip() for chunk in first))
        self.assertTrue(all(len(chunk.content) <= 45 for chunk in first))

    def test_empty_text_has_no_chunks(self) -> None:
        self.assertEqual(chunk_text("  \n\n "), [])

    def test_structure_aware_chunks_never_cross_sections(self) -> None:
        chunks = chunk_sections(
            (
                _section(
                    "introduction",
                    "1 Introduction",
                    "Introduction body explains the problem in one complete paragraph.\n\n"
                    "A second introduction paragraph provides the motivation and research context.",
                    page_start=1,
                    page_end=2,
                ),
                _section(
                    "method",
                    "2 Method",
                    "Method body describes aggregation and privacy protection in enough detail for retrieval.",
                    page_start=3,
                    page_end=4,
                    kind="method",
                ),
            ),
            max_chars=100,
            overlap_chars=25,
        )

        self.assertEqual({chunk.section_id for chunk in chunks}, {"introduction", "method"})
        self.assertTrue(
            all(
                "Introduction body" not in chunk.content or chunk.section_id == "introduction"
                for chunk in chunks
            )
        )
        self.assertEqual(next(chunk for chunk in chunks if chunk.section_id == "method").page_start, 3)

    def test_long_paragraph_splits_on_complete_sentence_boundaries(self) -> None:
        chunks = chunk_sections(
            (
                _section(
                    "method",
                    "2 Method",
                    "First complete sentence explains training. "
                    "Second complete sentence explains aggregation. "
                    "Third complete sentence explains privacy.",
                    kind="method",
                ),
            ),
            max_chars=58,
            overlap_chars=20,
        )

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.content.endswith(".") for chunk in chunks))
        self.assertTrue(all(not chunk.content.startswith("entence") for chunk in chunks))

    def test_embedding_context_does_not_change_raw_chunk_content(self) -> None:
        chunk = chunk_sections(
            (_section("method", "2 Method", "Raw original text.", kind="method"),),
            max_chars=100,
            overlap_chars=0,
        )[0]

        self.assertEqual(chunk.content, "Raw original text.")
        self.assertEqual(chunk.section_path, "2 Method")
        self.assertEqual(
            chunk.embedding_text("Paper title"),
            "Paper: Paper title\nSection: 2 Method\n\nRaw original text.",
        )

    def test_references_are_preserved_in_sections_but_not_retrieval_chunks(self) -> None:
        chunks = chunk_sections(
            (_section("references", "References", "[1] A cited paper.", kind="references"),),
            max_chars=100,
            overlap_chars=0,
        )

        self.assertEqual(chunks, [])


if __name__ == "__main__":
    unittest.main()
