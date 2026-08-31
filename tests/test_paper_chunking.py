from __future__ import annotations

import unittest

import app.papers.chunking as chunking
from app.papers.chunking import chunk_multimodal, chunk_sections, chunk_text
from app.papers.parsing import ParsedBlock, ParsedPage, ParsedPaper, ParsedSection


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
    def test_hierarchical_chunks_keep_source_text_and_add_retrieval_context(self) -> None:
        self.assertTrue(hasattr(chunking, "chunk_hierarchical"))
        before = ParsedBlock(
            1,
            "body",
            "The aggregation weights are proportional to local dataset sizes.",
            (10, 10, 500, 30),
            0,
            metadata={"block_id": "body-before"},
        )
        equation = ParsedBlock(
            1,
            "equation",
            "w^{t+1}=sum_i p_i w_i^t",
            (10, 40, 500, 80),
            1,
            metadata={
                "block_id": "eq-1",
                "label": "Equation 1",
                "markdown": "$$w^{t+1}=\\sum_i p_i w_i^t$$",
            },
        )
        after = ParsedBlock(
            1,
            "body",
            "Here p_i denotes the normalized weight of client i.",
            (10, 90, 500, 120),
            2,
            metadata={"block_id": "body-after"},
        )
        text = "\n\n".join(block.text for block in (before, equation, after))
        page = ParsedPage(1, text, "hash", len(text), "docling", "usable", (before, equation, after))
        section = _section("method", "2 Method", text, kind="method")
        parsed = ParsedPaper(
            full_text=text,
            pages=(page,),
            sections=(section,),
            metadata={},
            manifest={"parser": {"name": "scholar_hierarchical_v4", "version": "4"}},
            status="ready",
            quality_score=0.95,
        )

        chunks = chunking.chunk_hierarchical(parsed, target_tokens=30, max_tokens=80)
        formula = next(chunk for chunk in chunks if chunk.chunk_type == "equation")

        self.assertEqual(formula.content, "$$w^{t+1}=\\sum_i p_i w_i^t$$")
        self.assertEqual(formula.source_block_ids, ("eq-1",))
        self.assertIsNone(formula.parent_section_id)
        self.assertIn("aggregation weights", formula.context_before)
        self.assertIn("p_i denotes", formula.context_after)
        self.assertIn(formula.context_before, formula.embedding_content)
        self.assertIn(formula.context_after, formula.embedding_content)
        self.assertEqual(formula.metadata["provenance"]["page_number"], 1)

    def test_large_table_chunks_repeat_caption_and_header_by_complete_rows(self) -> None:
        self.assertTrue(hasattr(chunking, "chunk_hierarchical"))
        rows = ["| Method | Score |", "| --- | --- |"] + [
            f"| Model {index} | {80 + index}.0 |" for index in range(8)
        ]
        table = ParsedBlock(
            2,
            "table",
            "Table 2. Results",
            (10, 20, 500, 500),
            1,
            metadata={
                "block_id": "table-2",
                "label": "Table 2",
                "caption": "Table 2. Results",
                "markdown": "\n".join(rows),
            },
        )
        page = ParsedPage(2, table.text, "hash", len(table.text), "docling", "usable", (table,))
        section = _section("results", "4 Results", table.text, page_start=2, page_end=2, kind="experiment")
        parsed = ParsedPaper(table.text, (page,), (section,), {}, {}, "ready", 0.9)

        chunks = [
            chunk
            for chunk in chunking.chunk_hierarchical(parsed, target_tokens=12, max_tokens=24)
            if chunk.chunk_type == "table"
        ]

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.content.startswith("Table 2. Results\n\n| Method | Score |") for chunk in chunks))
        self.assertTrue(all(chunk.source_block_ids == ("table-2",) for chunk in chunks))
        rendered_rows = "\n".join(chunk.content for chunk in chunks)
        self.assertIn("| Model 0 | 80.0 |", rendered_rows)
        self.assertIn("| Model 7 | 87.0 |", rendered_rows)

    def test_hierarchical_prose_uses_soft_token_target_without_blind_overlap(self) -> None:
        sentences = [f"Sentence {index} explains federated aggregation clearly." for index in range(8)]
        text = " ".join(sentences)
        blocks = tuple(
            ParsedBlock(
                1, "body", sentence, (10, index * 20, 500, index * 20 + 15), index,
                metadata={"block_id": f"body-{index}"},
            )
            for index, sentence in enumerate(sentences)
        )
        page = ParsedPage(1, text, "hash", len(text), "docling", "usable", blocks)
        parsed = ParsedPaper(
            text,
            (page,),
            (_section("method", "2 Method", text, kind="method"),),
            {}, {}, "ready", 0.9,
        )

        chunks = [
            chunk
            for chunk in chunking.chunk_hierarchical(parsed, target_tokens=12, max_tokens=100)
            if chunk.chunk_type == "prose"
        ]

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.token_count <= 24 for chunk in chunks))
        combined = " ".join(chunk.content for chunk in chunks)
        self.assertTrue(all(combined.count(sentence) == 1 for sentence in sentences))

    def test_large_algorithm_splits_by_complete_steps_and_repeats_caption(self) -> None:
        steps = [f"{index}. Aggregate the update from client {index}." for index in range(1, 9)]
        algorithm_text = "\n".join([
            "Input: encrypted client updates",
            "Output: aggregated global model",
            *steps,
        ])
        algorithm = ParsedBlock(
            3,
            "algorithm",
            algorithm_text,
            (10, 20, 500, 600),
            1,
            metadata={
                "block_id": "algorithm-1",
                "label": "Algorithm 1",
                "caption": "Algorithm 1. Secure aggregation",
                "markdown": algorithm_text,
            },
        )
        page = ParsedPage(3, algorithm.text, "hash", len(algorithm.text), "docling", "usable", (algorithm,))
        section = _section("method", "3 Method", algorithm.text, page_start=3, page_end=3, kind="method")
        parsed = ParsedPaper(algorithm.text, (page,), (section,), {}, {}, "ready", 0.9)

        chunks = [
            chunk
            for chunk in chunking.chunk_hierarchical(parsed, target_tokens=10, max_tokens=24)
            if chunk.chunk_type == "algorithm"
        ]

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.content.startswith("Algorithm 1. Secure aggregation") for chunk in chunks))
        self.assertTrue(all("Input: encrypted client updates" in chunk.content for chunk in chunks))
        self.assertTrue(all("Output: aggregated global model" in chunk.content for chunk in chunks))
        combined = "\n".join(chunk.content for chunk in chunks)
        self.assertTrue(all(combined.count(step) == 1 for step in steps))

    def test_inline_colon_numbered_algorithm_restores_step_boundaries(self) -> None:
        markdown = (
            "```text\n"
            "1: Input: client updates 2: Output: global model "
            "3: Initialize the accumulator 4: for each client do aggregate its update "
            "5: return the global model\n"
            "```"
        )
        algorithm = ParsedBlock(
            1,
            "algorithm",
            markdown,
            (0, 0, 100, 100),
            0,
            metadata={
                "block_id": "algorithm-inline",
                "label": "Algorithm 1",
                "caption": "Aggregate client updates",
                "markdown": markdown,
            },
        )
        page = ParsedPage(1, markdown, "hash", len(markdown), "test", "usable", (algorithm,))
        parsed = ParsedPaper(
            markdown,
            (page,),
            (_section("method", "Method", markdown, kind="method"),),
            {},
            {},
            "ready",
            1.0,
        )

        chunks = [
            chunk
            for chunk in chunking.chunk_hierarchical(parsed, target_tokens=10, max_tokens=28)
            if chunk.chunk_type == "algorithm"
        ]

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.content.startswith("Algorithm 1. Aggregate client updates") for chunk in chunks))
        self.assertTrue(all("Input: client updates" in chunk.content for chunk in chunks))
        self.assertTrue(all("Output: global model" in chunk.content for chunk in chunks))
        self.assertTrue(all("```" not in chunk.content for chunk in chunks))
        combined = "\n".join(chunk.content for chunk in chunks)
        for step in (
            "3: Initialize the accumulator",
            "4: for each client do aggregate its update",
            "5: return the global model",
        ):
            self.assertEqual(combined.count(step), 1)

    def test_inline_bare_numbered_algorithm_restores_step_boundaries(self) -> None:
        markdown = (
            "```text\n"
            "1 Initialize the model 2 for each client do aggregate its update "
            "3 Verifier: check the encrypted result 4 return the global model\n"
            "```"
        )
        algorithm = ParsedBlock(
            1,
            "algorithm",
            markdown,
            (0, 0, 100, 100),
            0,
            metadata={
                "block_id": "algorithm-inline-bare",
                "label": "Algorithm 2",
                "caption": "Verify aggregation",
                "markdown": markdown,
            },
        )
        page = ParsedPage(1, markdown, "hash", len(markdown), "test", "usable", (algorithm,))
        parsed = ParsedPaper(
            markdown,
            (page,),
            (_section("method", "Method", markdown, kind="method"),),
            {},
            {},
            "ready",
            1.0,
        )

        chunks = [
            chunk
            for chunk in chunking.chunk_hierarchical(parsed, target_tokens=8, max_tokens=24)
            if chunk.chunk_type == "algorithm"
        ]

        combined = "\n".join(chunk.content for chunk in chunks)
        for step in (
            "1 Initialize the model",
            "2 for each client do aggregate its update",
            "3 Verifier: check the encrypted result",
            "4 return the global model",
        ):
            self.assertEqual(combined.count(step), 1)
        self.assertTrue(all(chunk.token_count <= 24 for chunk in chunks))

    def test_algorithm_without_caption_does_not_invent_source_text(self) -> None:
        algorithm = ParsedBlock(
            1,
            "algorithm",
            "1. Initialize weights.\n2. Aggregate updates.",
            (0, 0, 100, 100),
            0,
            metadata={"block_id": "algorithm-no-caption"},
        )
        page = ParsedPage(1, algorithm.text, "hash", 20, "docling", "usable", (algorithm,))
        parsed = ParsedPaper(
            algorithm.text,
            (page,),
            (_section("method", "Method", algorithm.text, kind="method"),),
            {}, {}, "ready", 1.0,
        )

        chunk = chunking.chunk_hierarchical(parsed, target_tokens=30, max_tokens=60)[0]

        self.assertNotIn("Algorithm\n", chunk.content)
        self.assertTrue(chunk.content.startswith("1. Initialize weights."))

    def test_prose_chunks_keep_source_ids_pages_and_global_reading_order(self) -> None:
        first = ParsedBlock(
            1,
            "body",
            "A very long first sentence explains the system architecture and its assumptions. "
            "A second sentence explains the client behavior and communication protocol.",
            (0, 0, 100, 50),
            0,
            metadata={"block_id": "body-page-1"},
        )
        equation = ParsedBlock(
            2,
            "equation",
            "x=y",
            (0, 10, 100, 40),
            0,
            metadata={"block_id": "eq-page-2", "markdown": "$$x=y$$", "label": "Eq. 1"},
        )
        second = ParsedBlock(
            2,
            "body",
            "Page two prose follows the equation and explains its variables in detail.",
            (0, 50, 100, 90),
            1,
            metadata={"block_id": "body-page-2"},
        )
        page1 = ParsedPage(1, first.text, "p1", 100, "docling", "usable", (first,))
        page2 = ParsedPage(2, equation.text + "\n" + second.text, "p2", 100, "docling", "usable", (equation, second))
        text = first.text + "\n\n" + equation.text + "\n\n" + second.text
        parsed = ParsedPaper(
            text,
            (page1, page2),
            (_section("method", "Method", text, page_start=1, page_end=2, kind="method"),),
            {}, {}, "ready", 1.0,
        )

        chunks = chunking.chunk_hierarchical(parsed, target_tokens=12, max_tokens=24)

        prose = [chunk for chunk in chunks if chunk.chunk_type == "prose"]
        self.assertTrue(all(chunk.source_block_ids for chunk in prose))
        self.assertTrue(all(chunk.page_start == chunk.page_end for chunk in prose))
        page_two_types = [chunk.chunk_type for chunk in chunks if chunk.page_start == 2]
        self.assertEqual(page_two_types[0], "equation")
        self.assertEqual(page_two_types[-1], "prose")

    def test_same_page_prose_before_equation_keeps_reading_order(self) -> None:
        heading = ParsedBlock(1, "heading", "2 Method", (0, 0, 100, 20), 0)
        prose = ParsedBlock(
            1,
            "body",
            "The server first prepares the client weights.",
            (0, 30, 100, 50),
            1,
            metadata={"block_id": "body-before-equation"},
        )
        equation = ParsedBlock(
            1,
            "equation",
            "w=sum_i p_i w_i",
            (0, 60, 100, 90),
            2,
            metadata={"block_id": "eq-after-prose", "label": "Eq. 1", "markdown": "$$w=\\sum_i p_i w_i$$"},
        )
        page = ParsedPage(1, "", "p", 100, "docling", "usable", (heading, prose, equation))
        text = prose.text + "\n\n" + equation.text
        parsed = ParsedPaper(text, (page,), (_section("method", "2 Method", text),), {}, {}, "ready", 1.0)

        chunks = chunking.chunk_hierarchical(parsed)

        self.assertEqual([chunk.chunk_type for chunk in chunks], ["prose", "equation"])

    def test_equation_context_stays_in_section_and_uses_complete_sentence(self) -> None:
        old = ParsedBlock(1, "body", "Previous section closing sentence.", (0, 0, 100, 20), 0)
        heading = ParsedBlock(1, "heading", "2 Method", (0, 30, 100, 50), 1)
        equation = ParsedBlock(
            1, "equation", "x=y", (0, 60, 100, 80), 2,
            metadata={"block_id": "eq", "markdown": "$$x=y$$", "label": "Eq. 1"},
        )
        explanation = ParsedBlock(
            1,
            "body",
            "Here x denotes the global model. This second sentence is unrelated.",
            (0, 90, 100, 120),
            3,
        )
        page = ParsedPage(1, "", "p", 100, "docling", "usable", (old, heading, equation, explanation))
        method_text = equation.text + "\n\n" + explanation.text
        parsed = ParsedPaper(
            method_text,
            (page,),
            (_section("method", "2 Method", method_text, kind="method"),),
            {}, {}, "ready", 1.0,
        )

        formula = next(
            chunk for chunk in chunking.chunk_hierarchical(parsed) if chunk.chunk_type == "equation"
        )

        self.assertEqual(formula.context_before, "")
        self.assertEqual(formula.context_after, "Here x denotes the global model.")
        self.assertNotIn("Previous section", formula.embedding_content)

    def test_figure_embedding_uses_explicit_reference_sentence_from_section(self) -> None:
        reference = ParsedBlock(
            1,
            "body",
            "As shown in Fig. 2, the verifier checks every encrypted update.",
            (0, 0, 100, 30),
            0,
            metadata={"block_id": "body-reference"},
        )
        figure = ParsedBlock(
            1,
            "figure",
            "System model",
            (0, 40, 100, 120),
            1,
            metadata={"block_id": "fig-2", "label": "Fig. 2", "caption": "Fig. 2. System model"},
        )
        page = ParsedPage(1, "", "p", 100, "docling", "usable", (reference, figure))
        text = reference.text + "\n\n" + figure.text
        parsed = ParsedPaper(text, (page,), (_section("method", "Method", text),), {}, {}, "ready", 1.0)

        visual = next(
            chunk for chunk in chunking.chunk_hierarchical(parsed) if chunk.chunk_type == "figure"
        )

        self.assertIn("As shown in Fig. 2", visual.embedding_content)

    def test_multimodal_visual_blocks_are_atomic_and_keep_provenance(self) -> None:
        body = ParsedBlock(2, "body", "Method prose remains searchable.", (10, 10, 200, 30), 0)
        table_markdown = "| Method | Score |\n| --- | --- |\n" + "\n".join(
            f"| model-{index} | {index} |" for index in range(12)
        )
        table = ParsedBlock(
            2,
            "table",
            "Table 1. Main comparison",
            (10, 40, 400, 300),
            1,
            metadata={
                "label": "Table 1",
                "caption": "Main comparison",
                "markdown": table_markdown,
            },
        )
        algorithm = ParsedBlock(
            3,
            "algorithm",
            "Algorithm 1. Aggregate client updates\nInput: updates\nOutput: global model",
            (10, 40, 400, 300),
            0,
            metadata={"label": "Algorithm 1", "caption": "Aggregate client updates"},
        )
        page2 = ParsedPage(2, "", "p2", 100, "test", "usable", (body, table))
        page3 = ParsedPage(3, "", "p3", 100, "test", "usable", (algorithm,))
        section = _section(
            "method",
            "2 Method",
            "Method prose remains searchable.\n\nTable 1. Main comparison\n\n"
            "Algorithm 1. Aggregate client updates\nInput: updates\nOutput: global model",
            page_start=2,
            page_end=3,
            kind="method",
        )
        parsed = ParsedPaper(
            full_text=section.text,
            pages=(page2, page3),
            sections=(section,),
            metadata={},
            manifest={"parser": {"name": "multimodal_aware_v3", "version": "3"}},
            status="ready",
            quality_score=1.0,
        )

        chunks = chunk_multimodal(parsed, max_chars=80, overlap_chars=0)

        table_chunks = [chunk for chunk in chunks if "[TABLE Table 1]" in chunk.content]
        algorithm_chunks = [chunk for chunk in chunks if "[ALGORITHM Algorithm 1]" in chunk.content]
        self.assertEqual(len(table_chunks), 1)
        self.assertIn("model-11", table_chunks[0].content)
        self.assertGreater(len(table_chunks[0].content), 80)
        self.assertEqual(table_chunks[0].page_start, 2)
        self.assertEqual(table_chunks[0].section_id, "method")
        self.assertEqual(len(algorithm_chunks), 1)
        self.assertIn("Output: global model", algorithm_chunks[0].content)

    def test_display_formula_is_an_atomic_chunk_unit(self) -> None:
        formula = "$$\n" + r"w_i = \sum_{j=1}^{n} \zeta_j^i w_j^i" + "\n" + r"\tag{2}" + "\n$$"
        chunks = chunk_sections(
            (
                _section(
                    "method",
                    "2 Method",
                    "The server aggregates all client updates.\n\n"
                    + formula
                    + "\n\nThe weights are normalized before aggregation.",
                    kind="method",
                ),
            ),
            max_chars=45,
            overlap_chars=0,
        )

        formula_chunks = [chunk for chunk in chunks if "\\sum" in chunk.content]
        self.assertEqual(len(formula_chunks), 1)
        self.assertEqual(formula_chunks[0].content.count("$$"), 2)
        self.assertIn(r"\tag{2}", formula_chunks[0].content)

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
