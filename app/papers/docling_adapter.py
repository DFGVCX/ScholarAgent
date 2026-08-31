from __future__ import annotations

from collections import Counter, defaultdict
import os
from pathlib import Path
import re
import threading
from typing import Any

from app.papers.parsing import (
    HIERARCHICAL_PARSER_NAME,
    HIERARCHICAL_PARSER_VERSION,
    ParsedBlock,
    ParsedPage,
    ParsedPaper,
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
    "code": "code",
    "algorithm": "algorithm",
    "list_item": "body",
    "paragraph": "body",
    "text": "body",
}
_HEADING_LABELS = {"title", "section_header", "heading"}
_DROP_LABELS = {
    "page_header",
    "page_footer",
    "caption",
    "footnote",
    "reference",
    "form",
}

_CONVERTER_CACHE: dict[str, Any] = {}
_CONVERTER_CACHE_LOCK = threading.Lock()
_CONVERTER_USE_LOCK = threading.Lock()


def _configured_artifacts_path() -> Path | None:
    value = os.getenv("DOCLING_ARTIFACTS_PATH", "").strip()
    return Path(value) if value else None


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
    artifacts_path = _configured_artifacts_path()
    if artifacts_path is not None:
        from app.papers.docling_models import inspect_artifacts

        report = inspect_artifacts(artifacts_path)
        if not report["ready"]:
            missing = ", ".join(str(item) for item in report.get("missing") or ())
            raise RuntimeError(
                "Docling artifacts are incomplete; run the docling_models prepare command"
                + (f" ({missing})" if missing else "")
            )
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:  # pragma: no cover - exercised by fallback contract
        raise RuntimeError("Docling is not installed") from exc

    options = (
        PdfPipelineOptions(artifacts_path=artifacts_path)
        if artifacts_path
        else PdfPipelineOptions()
    )
    options.do_ocr = False
    if hasattr(options, "do_table_structure"):
        options.do_table_structure = True
    if hasattr(options, "do_formula_enrichment"):
        options.do_formula_enrichment = True
    hierarchy_options = getattr(options, "heading_hierarchy_options", None)
    if hierarchy_options is not None and hasattr(hierarchy_options, "enabled"):
        hierarchy_options.enabled = True
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


def _converter_cache_key() -> str:
    artifacts_path = _configured_artifacts_path()
    return str(artifacts_path.resolve()) if artifacts_path is not None else "<docling-default-cache>"


def _get_converter() -> Any:
    """Reuse heavyweight Docling models for all PDFs using the same artifact root."""
    key = _converter_cache_key()
    with _CONVERTER_CACHE_LOCK:
        converter = _CONVERTER_CACHE.get(key)
        if converter is None:
            converter = _build_converter()
            _CONVERTER_CACHE[key] = converter
        return converter


def _clear_converter_cache() -> None:
    """Reset process-local converters for deterministic tests and config changes."""
    with _CONVERTER_CACHE_LOCK:
        _CONVERTER_CACHE.clear()


def _section_kind(title: str) -> str:
    normalized = re.sub(r"^(?:\d+(?:\.\d+)*|[ivxlcdm]+)[\s.、:：-]+", "", title.lower()).strip()
    aliases = {
        "abstract": "abstract",
        "introduction": "introduction",
        "related work": "related_work",
        "method": "method",
        "methods": "method",
        "methodology": "method",
        "experiments": "experiment",
        "results": "experiment",
        "discussion": "discussion",
        "conclusion": "conclusion",
        "references": "references",
        "appendix": "appendix",
    }
    return aliases.get(normalized.rstrip(".:："), "section")


def _stable_section_id(title: str, seen: Counter[str]) -> str:
    slug = re.sub(r"[^a-z0-9\u3400-\u9fff]+", "-", title.lower()).strip("-") or "section"
    slug = slug[:64]
    seen[slug] += 1
    return slug if seen[slug] == 1 else f"{slug}-{seen[slug]}"


def _build_docling_sections(pages: list[ParsedPage]) -> tuple[Any, ...]:
    from app.papers.parsing import ParsedSection

    seen: Counter[str] = Counter()
    stack: list[dict[str, Any]] = []
    drafts: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def finalize() -> None:
        nonlocal current
        if current is not None:
            current["text"] = "\n\n".join(current.pop("paragraphs")).strip()
            drafts.append(current)
        current = None

    for page in pages:
        for block in page.blocks:
            if block.block_type == "heading":
                finalize()
                level = max(1, int(block.metadata.get("hierarchy_level") or 1))
                while stack and int(stack[-1]["level"]) >= level:
                    stack.pop()
                section_id = _stable_section_id(block.text, seen)
                parent = stack[-1] if stack else None
                path_titles = [str(item["title"]) for item in stack] + [block.text]
                current = {
                    "section_id": section_id,
                    "kind": _section_kind(block.text),
                    "title": block.text,
                    "page_start": page.page_number,
                    "page_end": page.page_number,
                    "paragraphs": [],
                    "parent_section_id": parent["section_id"] if parent else None,
                    "section_path": " > ".join(path_titles),
                    "heading_level": level,
                }
                stack.append({
                    "section_id": section_id,
                    "title": block.text,
                    "level": level,
                })
                continue
            if current is None:
                current = {
                    "section_id": _stable_section_id("preamble", seen),
                    "kind": "preamble",
                    "title": "Preamble",
                    "page_start": page.page_number,
                    "page_end": page.page_number,
                    "paragraphs": [],
                    "parent_section_id": None,
                    "section_path": "Preamble",
                    "heading_level": 0,
                }
            current["page_end"] = page.page_number
            if block.text:
                current["paragraphs"].append(block.text)
    finalize()

    sections: list[ParsedSection] = []
    cursor = 0
    for index, draft in enumerate(drafts):
        rendered = (
            draft["text"]
            if draft["kind"] == "preamble"
            else f"{draft['title']}\n\n{draft['text']}".strip()
        )
        sections.append(
            ParsedSection(
                section_id=draft["section_id"],
                index=index,
                kind=draft["kind"],
                title=draft["title"],
                page_start=draft["page_start"],
                page_end=draft["page_end"],
                text=draft["text"],
                char_start=cursor,
                char_end=cursor + len(rendered),
                text_hash=_hash_text(draft["text"]),
                parent_section_id=draft["parent_section_id"],
                section_path=draft["section_path"],
                heading_level=draft["heading_level"],
            )
        )
        cursor += len(rendered) + 2
    return tuple(sections)


def _document_page_numbers(document: Any, populated: dict[int, list[ParsedBlock]]) -> list[int]:
    raw_pages = getattr(document, "pages", None)
    numbers: set[int] = set(populated)
    if isinstance(raw_pages, dict):
        numbers.update(int(number) for number in raw_pages if int(number) > 0)
    elif raw_pages is not None:
        try:
            numbers.update(range(1, len(raw_pages) + 1))
        except TypeError:
            pass
    return sorted(numbers or {1})


def parse_docling_pdf(path: Path, *, converter: Any | None = None) -> ParsedPaper:
    """Convert Docling output into ScholarAgent's stable paper model."""
    if converter is None:
        shared_converter = _get_converter()
        with _CONVERTER_USE_LOCK:
            result = shared_converter.convert(path)
    else:
        result = converter.convert(path)
    document = result.document
    by_page: dict[int, list[ParsedBlock]] = defaultdict(list)
    source_items = 0

    for source_items, entry in enumerate(document.iterate_items(), start=1):
        item, level = entry if isinstance(entry, tuple) else (entry, 0)
        source_label = _label(getattr(item, "label", "text"))
        if source_label in _DROP_LABELS:
            continue
        block_type = _LABEL_TYPES.get(source_label, "body")
        text, caption, markdown = _item_text(item, document, block_type)
        if block_type == "code" and re.search(
            r"(?i)\balgorithm\s+\d+\b", "\n".join(part for part in (caption, text) if part)
        ):
            block_type = "algorithm"
        if source_label in _HEADING_LABELS:
            block_type = "heading"
        if not text and not markdown:
            continue
        page_number, bbox = _provenance(item)
        reading_order = len(by_page[page_number])
        metadata = {
            "block_id": f"docling-p{page_number}-b{reading_order}",
            "source_engine": "docling",
            "source_label": source_label,
            "hierarchy_level": max(
                1,
                int(
                    getattr(item, "level", None)
                    or getattr(item, "heading_level", None)
                    or level
                    or 1
                ),
            ) if block_type == "heading" else int(level or 0),
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
    for page_number in _document_page_numbers(document, by_page):
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

    sections = _build_docling_sections(pages)
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
        "requested_parser": HIERARCHICAL_PARSER_NAME,
        "actual_parser": HIERARCHICAL_PARSER_NAME,
        "coverage": {
            "total_pages": len(pages),
            "pages_extracted": sum(bool(page.searchable_chars) for page in pages),
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
