from __future__ import annotations

import unittest

from app.papers.chunking import chunk_text


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


if __name__ == "__main__":
    unittest.main()
