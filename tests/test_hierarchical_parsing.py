from __future__ import annotations

from pathlib import Path
import importlib.util
import os
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import app.papers.parsing as parsing


class DoclingConfigurationTest(unittest.TestCase):
    def test_configured_artifacts_path_reads_environment(self) -> None:
        from app.papers.docling_adapter import _configured_artifacts_path

        with patch.dict(os.environ, {"DOCLING_ARTIFACTS_PATH": "/models/docling"}):
            self.assertEqual(_configured_artifacts_path(), Path("/models/docling"))

        with patch.dict(os.environ, {"DOCLING_ARTIFACTS_PATH": "   "}):
            self.assertIsNone(_configured_artifacts_path())

    def test_converter_is_reused_for_same_artifact_directory(self) -> None:
        from app.papers.docling_adapter import _clear_converter_cache, _get_converter

        converter = object()
        _clear_converter_cache()
        try:
            with (
                patch.dict(os.environ, {"DOCLING_ARTIFACTS_PATH": "/models/docling"}),
                patch("app.papers.docling_adapter._build_converter", return_value=converter) as build,
            ):
                self.assertIs(_get_converter(), converter)
                self.assertIs(_get_converter(), converter)

            build.assert_called_once()
        finally:
            _clear_converter_cache()

    def test_configured_incomplete_models_fail_before_converter_creation(self) -> None:
        from app.papers.docling_adapter import _build_converter

        report = {
            "ready": False,
            "missing": ["tableformer:docling-project--docling-models"],
        }
        with (
            patch.dict(os.environ, {"DOCLING_ARTIFACTS_PATH": "/models/docling"}),
            patch("app.papers.docling_models.inspect_artifacts", return_value=report),
        ):
            with self.assertRaisesRegex(RuntimeError, "Docling artifacts are incomplete"):
                _build_converter()


class _DoclingItem:
    def __init__(
        self,
        label: str,
        text: str,
        *,
        page: int = 1,
        bbox: tuple[float, float, float, float] = (10.0, 20.0, 300.0, 60.0),
        caption: str = "",
        markdown: str = "",
        image=None,
    ) -> None:
        self.label = label
        self.text = text
        self.prov = (SimpleNamespace(page_no=page, bbox=bbox),)
        self._caption = caption
        self._markdown = markdown
        self._image = image

    def caption_text(self, _document) -> str:
        return self._caption

    def export_to_markdown(self, _document) -> str:
        return self._markdown or self.text

    def get_image(self, _document):
        return self._image


class _DoclingDocument:
    def __init__(self, items: list[tuple[_DoclingItem, int]], *, page_count: int = 1) -> None:
        self._items = items
        self.pages = {number: SimpleNamespace() for number in range(1, page_count + 1)}

    def iterate_items(self):
        return iter(self._items)


class _Converter:
    def __init__(self, document: _DoclingDocument) -> None:
        self.document = document

    def convert(self, _path: Path):
        return SimpleNamespace(document=self.document)


class HierarchicalPdfParsingTest(unittest.TestCase):
    def test_docling_converter_enables_picture_images_without_enabling_ocr(self) -> None:
        """Catches a converter regression that omits picture crops or enables OCR."""
        from app.papers.docling_adapter import _build_converter

        class FakePipelineOptions:
            def __init__(self, **_kwargs) -> None:
                self.do_ocr = True
                self.do_table_structure = False
                self.do_formula_enrichment = False
                self.generate_picture_images = False
                self.generate_page_images = False
                self.heading_hierarchy_options = SimpleNamespace(enabled=False)

        class FakeDocumentConverter:
            def __init__(self, *, format_options) -> None:
                self.format_options = format_options

        fake_modules = {
            "docling": SimpleNamespace(),
            "docling.datamodel": SimpleNamespace(),
            "docling.datamodel.base_models": SimpleNamespace(InputFormat=SimpleNamespace(PDF="pdf")),
            "docling.datamodel.pipeline_options": SimpleNamespace(PdfPipelineOptions=FakePipelineOptions),
            "docling.document_converter": SimpleNamespace(
                DocumentConverter=FakeDocumentConverter,
                PdfFormatOption=lambda *, pipeline_options: SimpleNamespace(pipeline_options=pipeline_options),
            ),
        }
        with patch.dict(sys.modules, fake_modules):
            converter = _build_converter()

        options = converter.format_options["pdf"].pipeline_options
        self.assertTrue(options.generate_picture_images)
        self.assertTrue(options.generate_page_images)
        self.assertFalse(options.do_ocr)

    def test_docling_picture_writes_safe_asset_and_removes_image_placeholder(self) -> None:
        """Catches a picture export regression that leaks Docling placeholders or data URIs."""
        from app.papers.docling_adapter import parse_docling_pdf

        class Image:
            def save(self, target, format="PNG") -> None:
                self.target = Path(target)
                self.format = format
                Path(target).write_bytes(b"png-image")

        with TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "paper.pdf"
            image = Image()
            picture = _DoclingItem(
                "picture",
                "Image not available. Please use PdfPipelineOptions(generate_picture_images=True)",
                caption="Figure 1. Training workflow",
                markdown="![Figure](data:image/png;base64,AAAA)",
                image=image,
            )
            body = _DoclingItem("text", "The workflow description provides enough searchable content. " * 3)
            parsed = parse_docling_pdf(pdf, converter=_Converter(_DoclingDocument([(picture, 1), (body, 1)])))

            figure = parsed.pages[0].blocks[0]
            self.assertEqual(figure.block_type, "figure")
            self.assertEqual(figure.text, "Figure 1. Training workflow")
            self.assertEqual(figure.metadata["asset_name"], "page_001_figure_001.png")
            self.assertTrue(figure.metadata["source_image_available"])
            self.assertNotIn("Image not available", figure.metadata["markdown"])
            self.assertNotIn("data:image", figure.metadata["markdown"])
            self.assertEqual((pdf.parent / "paper_assets" / "page_001_figure_001.png").read_bytes(), b"png-image")
            self.assertEqual(parsed.manifest["asset_directory"], "paper_assets")
            self.assertEqual(parsed.to_manifest()["asset_inventory"][0]["name"], "page_001_figure_001.png")

    def test_docling_picture_without_image_keeps_caption_without_fake_asset(self) -> None:
        """Catches a missing-image fallback that invents an asset instead of preserving the caption."""
        from app.papers.docling_adapter import parse_docling_pdf

        with TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "paper.pdf"
            picture = _DoclingItem(
                "picture",
                "Image not available. Please use PdfPipelineOptions(generate_picture_images=True)",
                caption="Figure 2. Missing crop remains auditable",
                image=None,
            )
            body = _DoclingItem("text", "The associated explanatory paragraph has sufficient searchable text. " * 3)
            parsed = parse_docling_pdf(pdf, converter=_Converter(_DoclingDocument([(picture, 1), (body, 1)])))

            figure = parsed.pages[0].blocks[0]
            self.assertEqual(figure.text, "Figure 2. Missing crop remains auditable")
            self.assertFalse(figure.metadata["source_image_available"])
            self.assertNotIn("asset_name", figure.metadata)
            self.assertFalse((pdf.parent / "paper_assets").exists())

    def test_docling_image_only_picture_is_saved_and_inventory_backed(self) -> None:
        """Catches an empty-content gate that drops a real source image before export."""
        from app.papers.docling_adapter import parse_docling_pdf

        class Image:
            def save(self, target, format="PNG") -> None:
                Path(target).write_bytes(b"image-only")

        with TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "paper.pdf"
            parsed = parse_docling_pdf(
                pdf,
                converter=_Converter(_DoclingDocument([(_DoclingItem("picture", "", image=Image()), 1)])),
            )

            figure = parsed.pages[0].blocks[0]
            self.assertEqual(figure.block_type, "figure")
            self.assertTrue(figure.metadata["source_image_available"])
            self.assertEqual(figure.metadata["asset_name"], "page_001_figure_001.png")
            self.assertEqual((pdf.parent / "paper_assets" / figure.metadata["asset_name"]).read_bytes(), b"image-only")
            self.assertEqual(parsed.to_manifest()["asset_inventory"][0]["name"], figure.metadata["asset_name"])

    def test_docling_picture_comment_placeholder_is_removed_without_stripping_other_comments(self) -> None:
        """Catches the real Docling HTML placeholder leaving an empty comment in chunks."""
        from app.papers.docling_adapter import parse_docling_pdf

        with TemporaryDirectory() as temporary:
            placeholder = "<!-- 🖼️❌ Image not available. Please use PdfPipelineOptions(generate_picture_images=True) -->"
            picture = _DoclingItem(
                "picture",
                placeholder,
                caption="Figure 3. Caption survives",
                markdown=f"<!-- retained audit note -->\n{placeholder}",
            )
            parsed = parse_docling_pdf(
                Path(temporary) / "paper.pdf",
                converter=_Converter(_DoclingDocument([(picture, 1)])),
            )

            figure = parsed.pages[0].blocks[0]
            self.assertEqual(figure.text, "Figure 3. Caption survives")
            self.assertEqual(figure.metadata["markdown"], "<!-- retained audit note -->")

    def test_docling_picture_placeholder_cleanup_preserves_surrounding_text(self) -> None:
        """Catches cleanup that removes a diagnostic phrase quoted inside ordinary text/comments."""
        from app.papers.docling_adapter import parse_docling_pdf

        phrase = "Image not available. Please use PdfPipelineOptions(generate_picture_images=True)"
        with TemporaryDirectory() as temporary:
            picture = _DoclingItem(
                "picture",
                f"The manual quotes: {phrase}. Continue reading.",
                caption="Figure 6. Quoted diagnostic",
                markdown=f"<!-- audit says {phrase}; retain this note -->",
            )
            parsed = parse_docling_pdf(
                Path(temporary) / "paper.pdf",
                converter=_Converter(_DoclingDocument([(picture, 1)])),
            )

            figure = parsed.pages[0].blocks[0]
            self.assertEqual(figure.text, f"The manual quotes: {phrase}. Continue reading.")
            self.assertEqual(figure.metadata["markdown"], f"<!-- audit says {phrase}; retain this note -->")

    def test_docling_picture_no_icon_placeholder_comment_is_preserved(self) -> None:
        """Catches cleanup treating a no-icon comment as Docling's emoji placeholder."""
        from app.papers.docling_adapter import parse_docling_pdf

        no_icon_comment = "<!-- Image not available. Please use PdfPipelineOptions(generate_picture_images=True) -->"
        with TemporaryDirectory() as temporary:
            picture = _DoclingItem(
                "picture",
                no_icon_comment,
                caption="Figure 7. No-icon note",
                markdown=no_icon_comment,
            )
            parsed = parse_docling_pdf(
                Path(temporary) / "paper.pdf",
                converter=_Converter(_DoclingDocument([(picture, 1)])),
            )

            figure = parsed.pages[0].blocks[0]
            self.assertEqual(figure.text, no_icon_comment)
            self.assertEqual(figure.metadata["markdown"], no_icon_comment)

    def test_docling_picture_asset_root_symlink_is_not_followed(self) -> None:
        """Catches image export writing through a pre-existing assets-directory symlink."""
        from app.papers.docling_adapter import parse_docling_pdf

        class Image:
            def save(self, target, format="PNG") -> None:
                Path(target).write_bytes(b"must-not-write")

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "paper.pdf"
            external = root / "external"
            external.mkdir()
            asset_root = root / "paper_assets"
            try:
                asset_root.symlink_to(external, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")
            parsed = parse_docling_pdf(
                pdf,
                converter=_Converter(_DoclingDocument([(_DoclingItem("picture", "", image=Image()), 1)])),
            )

            figure = parsed.pages[0].blocks[0]
            self.assertFalse((external / "page_001_figure_001.png").exists())
            self.assertEqual(figure.metadata["source_image_error"], "image_write_target_unsafe")

    def test_docling_picture_save_falls_back_to_png_suffix_without_format_argument(self) -> None:
        """Catches temporary names that prevent extension-driven PNG writers from succeeding."""
        from app.papers.docling_adapter import parse_docling_pdf

        class ExtensionDrivenImage:
            def save(self, target, format=None) -> None:
                if format is not None:
                    raise TypeError("format keyword unsupported")
                self.assertEqual(Path(target).suffix, ".png")
                Path(target).write_bytes(b"extension-png")

            def assertEqual(self, actual, expected) -> None:
                if actual != expected:
                    raise AssertionError(f"{actual!r} != {expected!r}")

        with TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "paper.pdf"
            parsed = parse_docling_pdf(
                pdf,
                converter=_Converter(_DoclingDocument([(_DoclingItem("picture", "", image=ExtensionDrivenImage()), 1)])),
            )

            figure = parsed.pages[0].blocks[0]
            self.assertTrue(figure.metadata["source_image_available"])
            self.assertEqual((pdf.parent / "paper_assets" / figure.metadata["asset_name"]).read_bytes(), b"extension-png")

    def test_docling_picture_save_failure_preserves_existing_asset(self) -> None:
        """Catches a failed write deleting or corrupting the prior deterministic asset."""
        from app.papers.docling_adapter import parse_docling_pdf

        class FailingImage:
            def save(self, target, format="PNG") -> None:
                Path(target).write_bytes(b"partial-new-bytes")
                raise OSError("disk write failed")

        with TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "paper.pdf"
            asset = pdf.parent / "paper_assets" / "page_001_figure_001.png"
            asset.parent.mkdir()
            asset.write_bytes(b"previous-asset")
            parsed = parse_docling_pdf(
                pdf,
                converter=_Converter(_DoclingDocument([(_DoclingItem("picture", "", image=FailingImage()), 1)])),
            )

            figure = parsed.pages[0].blocks[0]
            self.assertEqual(asset.read_bytes(), b"previous-asset")
            self.assertFalse(figure.metadata["source_image_available"])
            self.assertEqual(figure.metadata["source_image_error"], "image_write_failed")

    def test_docling_picture_extraction_error_is_auditable_but_missing_image_is_not_error(self) -> None:
        """Catches image extraction failures being silently conflated with an absent image."""
        from app.papers.docling_adapter import parse_docling_pdf

        class BrokenPicture(_DoclingItem):
            def get_image(self, _document):
                raise RuntimeError("private source path must not leak")

        with TemporaryDirectory() as temporary:
            parsed = parse_docling_pdf(
                Path(temporary) / "paper.pdf",
                converter=_Converter(
                    _DoclingDocument(
                        [
                            (BrokenPicture("picture", "", caption="Figure 4. Broken source"), 1),
                            (_DoclingItem("picture", "", caption="Figure 5. No image"), 1),
                        ]
                    )
                ),
            )

            extraction_error, no_image = parsed.pages[0].blocks
            self.assertEqual(extraction_error.metadata["source_image_error"], "image_extraction_failed")
            self.assertNotIn("private source path", str(extraction_error.metadata))
            self.assertNotIn("source_image_error", no_image.metadata)

    def test_docling_table_preserves_tableformer_markdown_and_writes_source_asset(self) -> None:
        """Catches tables losing exact TableFormer cells or their source-image fallback."""
        from app.papers.docling_adapter import parse_docling_pdf

        class Image:
            def save(self, target, format="PNG") -> None:
                Path(target).write_bytes(b"table-image")

        markdown = "\n| Method | Score |\n| --- | --- |\n| Scholar | 0.91 |\n"
        with TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "paper.pdf"
            table = _DoclingItem("table", "", markdown=markdown, image=Image())
            parsed = parse_docling_pdf(
                pdf,
                converter=_Converter(_DoclingDocument([(table, 1)])),
            )

            block = parsed.pages[0].blocks[0]
            self.assertEqual(block.block_type, "table")
            self.assertEqual(block.text, markdown)
            self.assertEqual(block.metadata["markdown"], markdown)
            self.assertEqual(block.metadata["asset_name"], "page_001_table_001.png")
            self.assertTrue(block.metadata["source_image_available"])
            self.assertEqual(
                (pdf.parent / "paper_assets" / "page_001_table_001.png").read_bytes(),
                b"table-image",
            )
            inventory = parsed.to_manifest()["asset_inventory"]
            self.assertEqual(len(inventory), 1)
            self.assertEqual(inventory[0]["name"], "page_001_table_001.png")
            self.assertEqual(inventory[0]["type"], "table")
            self.assertEqual(inventory[0]["page_number"], 1)

    def test_docling_table_without_image_keeps_markdown_without_fake_asset(self) -> None:
        """Catches a no-crop table fallback that discards cells or invents an asset."""
        from app.papers.docling_adapter import parse_docling_pdf

        markdown = "| Metric | Value |\n| --- | --- |\n| F1 | 0.88 |"
        with TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "paper.pdf"
            parsed = parse_docling_pdf(
                pdf,
                converter=_Converter(
                    _DoclingDocument([(_DoclingItem("table", "", markdown=markdown), 1)])
                ),
            )

            block = parsed.pages[0].blocks[0]
            self.assertEqual(block.text, markdown)
            self.assertEqual(block.metadata["markdown"], markdown)
            self.assertFalse(block.metadata["source_image_available"])
            self.assertNotIn("asset_name", block.metadata)
            self.assertFalse((pdf.parent / "paper_assets").exists())

    def test_docling_table_prefers_exact_markdown_over_caption_and_retains_caption_label(self) -> None:
        """Catches TableFormer cells being replaced by a common TableItem caption."""
        from app.papers.docling_adapter import parse_docling_pdf

        markdown = "\n| Method | Score |\n| --- | --- |\n| Scholar | 0.91 |\n"
        with TemporaryDirectory() as temporary:
            parsed = parse_docling_pdf(
                Path(temporary) / "paper.pdf",
                converter=_Converter(
                    _DoclingDocument(
                        [
                            (
                                _DoclingItem(
                                    "table",
                                    "",
                                    caption="Table 1. Main results",
                                    markdown=markdown,
                                ),
                                1,
                            )
                        ]
                    )
                ),
            )

            table = parsed.pages[0].blocks[0]
            self.assertEqual(table.text, markdown)
            self.assertEqual(table.metadata["markdown"], markdown)
            self.assertEqual(table.metadata["caption"], "Table 1. Main results")
            self.assertEqual(table.metadata["label"], "Table 1. Main results")

    def test_docling_non_table_markdown_remains_trimmed(self) -> None:
        """Catches byte-preservation for tables leaking into other Docling export types."""
        from app.papers.docling_adapter import parse_docling_pdf

        parsed = parse_docling_pdf(
            Path("paper.pdf"),
            converter=_Converter(
                _DoclingDocument(
                    [(_DoclingItem("equation", "", markdown="\n  $$x = y$$  \n"), 1)]
                )
            ),
        )

        equation = parsed.pages[0].blocks[0]
        self.assertEqual(equation.text, "$$x = y$$")
        self.assertEqual(equation.metadata["markdown"], "$$x = y$$")

    def test_docling_table_image_extraction_and_write_failures_are_auditable(self) -> None:
        """Catches table crop failures becoming sensitive exceptions or silent success."""
        from app.papers.docling_adapter import parse_docling_pdf

        class BrokenTable(_DoclingItem):
            def get_image(self, _document):
                raise RuntimeError("private source path must not leak")

        class FailingImage:
            def save(self, _target, format="PNG") -> None:
                raise OSError("private target path must not leak")

        markdown = "| A | B |\n| --- | --- |\n| 1 | 2 |"
        with TemporaryDirectory() as temporary:
            parsed = parse_docling_pdf(
                Path(temporary) / "paper.pdf",
                converter=_Converter(
                    _DoclingDocument(
                        [
                            (BrokenTable("table", "", markdown=markdown), 1),
                            (_DoclingItem("table", "", markdown=markdown, image=FailingImage()), 1),
                        ]
                    )
                ),
            )

            extraction_error, write_error = parsed.pages[0].blocks
            self.assertEqual(extraction_error.metadata["source_image_error"], "image_extraction_failed")
            self.assertEqual(write_error.metadata["source_image_error"], "image_write_failed")
            self.assertFalse(extraction_error.metadata["source_image_available"])
            self.assertFalse(write_error.metadata["source_image_available"])
            self.assertNotIn("private source path", str(extraction_error.metadata))
            self.assertNotIn("private target path", str(write_error.metadata))

    def test_docling_prose_algorithm_mention_does_not_retype_following_code(self) -> None:
        """Catches prose mentions of Algorithm N being treated as adjacent algorithm headings."""
        from app.papers.docling_adapter import parse_docling_pdf

        parsed = parse_docling_pdf(
            Path("paper.pdf"),
            converter=_Converter(
                _DoclingDocument(
                    [
                        (_DoclingItem("text", "We compare with Algorithm 1 in the related work."), 1),
                        (_DoclingItem("code", "return baseline_result"), 1),
                    ]
                )
            ),
        )

        self.assertEqual(parsed.pages[0].blocks[1].block_type, "code")

    def test_docling_adjacent_algorithm_heading_classifies_only_following_code(self) -> None:
        """Catches a classifier that misses adjacent algorithm titles or upgrades distant code."""
        from app.papers.docling_adapter import parse_docling_pdf

        document = _DoclingDocument(
            [
                (_DoclingItem("section_header", "Algorithm 1: Federated training"), 1),
                (_DoclingItem("code", "1: Initialize model\n2: Train clients\n3: Aggregate updates"), 1),
                (_DoclingItem("text", "The following source fragment is ordinary implementation code."), 1),
                (_DoclingItem("code", "return model_state"), 1),
            ]
        )

        parsed = parse_docling_pdf(Path("paper.pdf"), converter=_Converter(document))

        algorithm, ordinary_code = [
            block for block in parsed.pages[0].blocks if block.block_type in {"algorithm", "code"}
        ]
        self.assertEqual(algorithm.metadata["label"], "Algorithm 1: Federated training")
        self.assertEqual(algorithm.metadata["caption"], "Algorithm 1: Federated training")
        self.assertEqual(algorithm.text, "1: Initialize model\n2: Train clients\n3: Aggregate updates")
        self.assertEqual(ordinary_code.block_type, "code")
        self.assertEqual(ordinary_code.text, "return model_state")

    def test_docling_bottom_left_bbox_is_normalized_to_top_left(self) -> None:
        from app.papers.docling_adapter import parse_docling_pdf

        class BottomLeftBBox:
            l = 10.0
            t = 700.0
            r = 110.0
            b = 650.0

            def to_top_left_origin(self, page_height: float):
                return SimpleNamespace(
                    l=self.l,
                    t=page_height - self.t,
                    r=self.r,
                    b=page_height - self.b,
                )

        body = _DoclingItem(
            "text",
            "The server aggregates client updates while preserving complete source provenance. " * 3,
            bbox=BottomLeftBBox(),
        )
        document = _DoclingDocument(
            [
                (_DoclingItem("section_header", "2 Method"), 1),
                (body, 2),
            ]
        )
        document.pages[1].size = SimpleNamespace(height=800.0)

        parsed = parse_docling_pdf(Path("paper.pdf"), converter=_Converter(document))

        normalized = next(block for block in parsed.pages[0].blocks if block.block_type == "body")
        self.assertEqual(normalized.bbox, (10.0, 100.0, 110.0, 150.0))

    def test_sparse_page_mapping_never_uses_previous_page_height(self) -> None:
        from app.papers.docling_adapter import parse_docling_pdf

        class BottomLeftBBox:
            l = 10.0
            t = 700.0
            r = 110.0
            b = 650.0

            def to_top_left_origin(self, page_height: float):
                return SimpleNamespace(
                    l=self.l,
                    t=page_height - self.t,
                    r=self.r,
                    b=page_height - self.b,
                )

        body = _DoclingItem(
            "text",
            "This second-page paragraph has enough searchable text for provenance validation. " * 3,
            page=2,
            bbox=BottomLeftBBox(),
        )
        document = _DoclingDocument([(body, 1)], page_count=2)
        document.pages = {1: SimpleNamespace(size=SimpleNamespace(height=1000.0))}

        parsed = parse_docling_pdf(Path("paper.pdf"), converter=_Converter(document))

        second_page = next(page for page in parsed.pages if page.page_number == 2)
        self.assertEqual(second_page.blocks[0].bbox, (10.0, 700.0, 110.0, 650.0))

    def test_docling_bbox_conversion_failure_is_not_silently_ignored(self) -> None:
        from app.papers.docling_adapter import parse_docling_pdf

        class BrokenBBox:
            l = 10.0
            t = 700.0
            r = 110.0
            b = 650.0

            def to_top_left_origin(self, page_height: float):
                raise ValueError(f"cannot normalize at height {page_height}")

        item = _DoclingItem(
            "text",
            "This paragraph is long enough to pass the searchable text quality gate. " * 3,
            bbox=BrokenBBox(),
        )
        document = _DoclingDocument([(item, 1)])
        document.pages[1].size = SimpleNamespace(height=800.0)

        with self.assertRaisesRegex(ValueError, "cannot normalize"):
            parse_docling_pdf(Path("paper.pdf"), converter=_Converter(document))

    def test_docling_output_is_normalized_without_leaking_docling_types(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("app.papers.docling_adapter"))
        from app.papers.docling_adapter import parse_docling_pdf

        document = _DoclingDocument(
            [
                (_DoclingItem("section_header", "2 Method"), 1),
                (_DoclingItem("text", "The server aggregates all client updates."), 2),
                (
                    _DoclingItem(
                        "formula",
                        "w^{t+1}=sum_i p_i w_i^t",
                        caption="Equation 1",
                        markdown="$$w^{t+1}=\\sum_i p_i w_i^t$$",
                    ),
                    2,
                ),
                (
                    _DoclingItem(
                        "table",
                        "Method Score FedAvg 82.4",
                        caption="Table 1. Main results",
                        markdown="| Method | Score |\n| --- | --- |\n| FedAvg | 82.4 |",
                    ),
                    2,
                ),
            ]
        )

        parsed = parse_docling_pdf(Path("paper.pdf"), converter=_Converter(document))

        self.assertEqual(parsed.status, "ready")
        self.assertEqual(parsed.manifest["parser"]["engine"], "docling")
        self.assertEqual(parsed.manifest["requested_parser"], parsing.HIERARCHICAL_PARSER_NAME)
        self.assertEqual(parsed.manifest["actual_parser"], parsing.HIERARCHICAL_PARSER_NAME)
        self.assertEqual(parsed.sections[0].title, "2 Method")
        typed = {block.block_type: block for block in parsed.pages[0].blocks}
        self.assertEqual(typed["equation"].metadata["markdown"], "$$w^{t+1}=\\sum_i p_i w_i^t$$")
        self.assertIn("| FedAvg | 82.4 |", typed["table"].metadata["markdown"])
        self.assertEqual(typed["table"].metadata["source_engine"], "docling")
        self.assertTrue(all(isinstance(block, parsing.ParsedBlock) for block in parsed.pages[0].blocks))

    def test_docling_preserves_nested_heading_paths_and_filters_noise(self) -> None:
        from app.papers.docling_adapter import parse_docling_pdf

        document = _DoclingDocument(
            [
                (_DoclingItem("page_header", "IEEE TRANSACTIONS ON TEST"), 0),
                (_DoclingItem("section_header", "2 Method"), 1),
                (_DoclingItem("text", "Method overview explains the complete training and aggregation workflow."), 2),
                (_DoclingItem("section_header", "2.3 Threat Model"), 2),
                (_DoclingItem("list_item", "The adversary observes encrypted updates but cannot inspect private training data."), 3),
                (_DoclingItem("caption", "Fig. 1. Detached caption"), 3),
                (_DoclingItem("code", "x = model(update)"), 3),
            ]
        )

        parsed = parse_docling_pdf(Path("paper.pdf"), converter=_Converter(document))

        nested = next(section for section in parsed.sections if section.title == "2.3 Threat Model")
        self.assertEqual(nested.parent_section_id, parsed.sections[0].section_id)
        self.assertEqual(nested.section_path, "2 Method > 2.3 Threat Model")
        self.assertNotIn("IEEE TRANSACTIONS", parsed.full_text)
        self.assertNotIn("Detached caption", parsed.full_text)
        self.assertIn("The adversary observes encrypted updates", nested.text)
        code = next(block for block in parsed.pages[0].blocks if block.text == "x = model(update)")
        self.assertEqual(code.block_type, "code")

    def test_docling_quality_gate_counts_pages_without_items(self) -> None:
        from app.papers.docling_adapter import parse_docling_pdf

        document = _DoclingDocument(
            [(_DoclingItem("text", "This is the only page with searchable text. " * 5), 1)],
            page_count=5,
        )

        parsed = parse_docling_pdf(Path("paper.pdf"), converter=_Converter(document))

        self.assertEqual(len(parsed.pages), 5)
        self.assertEqual(parsed.manifest["coverage"]["total_pages"], 5)
        self.assertEqual(parsed.manifest["coverage"]["pages_extracted"], 1)
        self.assertEqual(parsed.status, "needs_ocr")

    def test_hierarchical_parser_falls_back_and_records_docling_failure(self) -> None:
        self.assertTrue(hasattr(parsing, "parse_pdf_hierarchical"))
        fallback = parsing.ParsedPaper(
            full_text="Fallback searchable paper text.",
            pages=(),
            sections=(),
            metadata={},
            manifest={"parser": {"name": parsing.MULTIMODAL_PARSER_NAME, "version": "3"}},
            status="ready",
            quality_score=0.8,
        )

        with (
            patch("app.papers.docling_adapter.parse_docling_pdf", side_effect=RuntimeError("model unavailable")),
            patch("app.papers.parsing.parse_pdf_multimodal", return_value=fallback),
        ):
            parsed = parsing.parse_pdf_hierarchical(Path("paper.pdf"))

        self.assertEqual(parsed.status, "ready")
        self.assertEqual(parsed.manifest["parser"]["name"], parsing.MULTIMODAL_PARSER_NAME)
        self.assertEqual(parsed.manifest["parser"]["engine"], "pymupdf_multimodal")
        self.assertEqual(parsed.manifest["requested_parser"], parsing.HIERARCHICAL_PARSER_NAME)
        self.assertEqual(parsed.manifest["actual_parser"], parsing.MULTIMODAL_PARSER_NAME)
        self.assertIn("model unavailable", parsed.manifest["fallback_reason"])
        self.assertEqual(parsed.manifest["fallback"]["from"], "docling")
        self.assertIn("model unavailable", parsed.manifest["fallback"]["reason"])
        self.assertIn("parser_fallback", parsed.warnings)

    def test_hierarchical_parser_contains_docling_system_exit(self) -> None:
        fallback = parsing.ParsedPaper(
            full_text="Fallback text remains available.",
            pages=(),
            sections=(),
            metadata={},
            manifest={"parser": {"name": parsing.MULTIMODAL_PARSER_NAME, "version": "3"}},
            status="ready",
            quality_score=0.8,
        )
        with (
            patch("app.papers.docling_adapter.parse_docling_pdf", side_effect=SystemExit(1)),
            patch("app.papers.parsing.parse_pdf_multimodal", return_value=fallback),
        ):
            try:
                parsed = parsing.parse_pdf_hierarchical(Path("paper.pdf"))
            except SystemExit:
                self.fail("a missing Docling model must not terminate the ingestion worker")

        self.assertEqual(parsed.status, "ready")
        self.assertEqual(parsed.manifest["fallback"]["reason"], "Docling exited with status 1")

    def test_hierarchical_fallback_reason_is_sanitized_and_bounded(self) -> None:
        fallback = parsing.ParsedPaper("ok", (), (), {}, {"parser": {}}, "ready", 0.8)
        secret_path = r"C:\\Users\\Redmi\\private\\model.bin"
        with (
            patch(
                "app.papers.docling_adapter.parse_docling_pdf",
                side_effect=RuntimeError(f"failed at {secret_path} " + "x" * 2000),
            ),
            patch("app.papers.parsing.parse_pdf_multimodal", return_value=fallback),
        ):
            parsed = parsing.parse_pdf_hierarchical(Path("paper.pdf"))

        reason = parsed.manifest["fallback_reason"]
        self.assertNotIn("Redmi", reason)
        self.assertLessEqual(len(reason), 500)


if __name__ == "__main__":
    unittest.main()
