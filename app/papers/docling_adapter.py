from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re
from typing import Any

from app.papers.parsing import (
    HIERARCHICAL_PARSER_NAME,
    HIERARCHICAL_PARSER_VERSION,
    ParsedBlock,
    ParsedPage,
    ParsedPaper,
    _build_sections,
    _document_metadata,
    _hash_text,
    _render_sections,
)


_LABEL_TYPES = {
    "formula": "equation",
    "equation": "equation",
    "table": "table",
    "picture": "figure",
    "figure": "figure",
    "code": "algorithm",
    "algorithm": "algorithm",
}
_HEADING_LABELS = {"title", "section_header", "heading"}


def _label(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "text").strip().lower().replace("-", "_")


def _call_export(item: Any, name: str, document: Any) -> str:
    method = getattr(item, name, None)
    if not callable(method):
        return ""
    for args, kwargs in (((document,), {}), ((), {"doc": document}), ((), {})):
        try:
            return str(method(*args, **kwargs) or "").strip()
        except TypeError:
            continue
    return ""


def _bbox_tuple(value: Any) -> tuple[float, float, float, float]:
    if value is None:
        return (0.0, 0.0, 0.0, 0.0)
    if isinstance(value, (tuple, list)) and len(value) >= 4:
        return tuple(float(part) for part in value[:4])  # type: ignore[return-value]
    for names in (("l", "t", "r", "b"), ("left", "top", "right", "bottom"), ("x0", "y0", "x1", "y1")):
        if all(hasattr(value, name) for name in names):
            return tuple(float(getattr(value, name)) for name in names)  # type: ignore[return-value]
    return (0.0, 0.0, 0.0, 0.0)


def _provenance(item: Any) -> tuple[int, tuple[float, float, float, float]]:
    records = tuple(getattr(item, "prov", ()) or ())
    if not records:
        return 1, (0.0, 0.0, 0.0, 0.0)
    record = records[0]
    page_number = max(1, int(getattr(record, "page_no", 1) or 1))
    return page_number, _bbox_tuple(getattr(record, "bbox", None))


def _item_text(item: Any, document: Any, block_type: str) -> tuple[str, str, str]:
    raw_text = str(getattr(item, "text", "") or "").strip()
    caption = _call_export(item, "caption_text", document)
    markdown = _call_export(item, "export_to_markdown", document)
    if block_type == "equation" and markdown:
        raw_text = raw_text or markdown
    elif block_type in {"table", "figure", "algorithm"}:
        raw_text = raw_text or caption or markdown
    return raw_text, caption, markdown


def _build_converter() -> Any:
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:  # pragma: no cover - exercised by fallback contract
        raise RuntimeError("Docling is not installed") from exc

    options = PdfPipelineOptions()
    options.do_ocr = False
    if hasattr(options, "do_table_structure"):
        options.do_table_structure = True
    if hasattr(options, "do_formula_enrichment"):
        options.do_formula_enrichment = True
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


def parse_docling_pdf(path: Path, *, converter: Any | None = None) -> ParsedPaper:
    """Convert Docling output into ScholarAgent's stable paper model."""
    result = (converter or _build_converter()).convert(path)
    document = result.document
    by_page: dict[int, list[ParsedBlock]] = defaultdict(list)
    source_items = 0

    for source_items, entry in enumerate(document.iterate_items(), start=1):
        item, level = entry if isinstance(entry, tuple) else (entry, 0)
        source_label = _label(getattr(item, "label", "text"))
        block_type = _LABEL_TYPES.get(source_label, "body")
        text, caption, markdown = _item_text(item, document, block_type)
        if not text and not markdown:
            continue
        page_number, bbox = _provenance(item)
        reading_order = len(by_page[page_number])
        metadata = {
            "block_id": f"docling-p{page_number}-b{reading_order}",
            "source_engine": "docling",
            "source_label": source_label,
            "hierarchy_level": int(level or 0),
            "caption": caption,
            "markdown": markdown,
        }
        label_text = caption or text
        if block_type in {"equation", "table", "figure", "algorithm"}:
            metadata["label"] = label_text.splitlines()[0][:160]
        by_page[page_number].append(
            ParsedBlock(
                page_number=page_number,
                block_type=block_type,
                text=text,
                bbox=bbox,
                reading_order=reading_order,
                font_size=16.0 if source_label in _HEADING_LABELS else 10.0,
                metadata=metadata,
            )
        )

    pages: list[ParsedPage] = []
    for page_number in sorted(by_page):
        blocks = tuple(by_page[page_number])
        text = "\n\n".join(block.text for block in blocks if block.text).strip()
        searchable_chars = len(re.sub(r"\s+", "", text))
        pages.append(
            ParsedPage(
                page_number=page_number,
                text=text,
                text_hash=_hash_text(text),
                searchable_chars=searchable_chars,
                extraction_method="docling",
                quality_status="usable" if searchable_chars >= 40 else "low_text",
                blocks=blocks,
            )
        )

    sections = _build_sections(pages)
    full_text = _render_sections(sections)
    total_chars = len(re.sub(r"\s+", "", full_text))
    low_text_pages = sum(page.searchable_chars < 40 for page in pages)
    insufficient = total_chars < 80 or (len(pages) > 1 and low_text_pages * 2 >= len(pages))
    status = "needs_ocr" if insufficient else "ready"
    metadata = _document_metadata(full_text, {})
    manifest = {
        "parser": {
            "name": HIERARCHICAL_PARSER_NAME,
            "version": HIERARCHICAL_PARSER_VERSION,
            "engine": "docling",
        },
        "coverage": {
            "total_pages": len(pages),
            "pages_extracted": len(pages),
            "low_text_pages": low_text_pages,
            "source_items": source_items,
            "text_truncated": False,
        },
        "text_hash": _hash_text(full_text),
        "language": metadata["language"],
        "ocr_enabled": False,
    }
    usable_ratio = (len(pages) - low_text_pages) / max(1, len(pages))
    quality_score = round(usable_ratio * min(1.0, total_chars / 1000.0), 6)
    return ParsedPaper(
        full_text=full_text if status == "ready" else "",
        pages=tuple(pages),
        sections=sections if status == "ready" else (),
        metadata=metadata,
        manifest=manifest,
        status=status,
        quality_score=quality_score,
        warnings=("searchable_text_insufficient",) if insufficient else (),
    )
