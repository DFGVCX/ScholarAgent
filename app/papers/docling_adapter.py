from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import re
import tempfile
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
_PICTURE_PLACEHOLDER = (
    "Image not available. Please use PdfPipelineOptions(generate_picture_images=True)"
)
_PICTURE_PLACEHOLDER_COMMENT_RE = re.compile(
    r"<!--\s*🖼(?:\ufe0f)?\s*❌\s*"
    + re.escape(_PICTURE_PLACEHOLDER)
    + r"\s*-->",
)
_DATA_URI_RE = re.compile(r"data:image/[^\s)\]}>]+", re.IGNORECASE)
_ALGORITHM_HEADING_RE = re.compile(r"(?i)\balgorithm\s+\d+\b")

_CONVERTER_CACHE: dict[str, Any] = {}
_CONVERTER_CACHE_LOCK = threading.Lock()
_CONVERTER_USE_LOCK = threading.Lock()


def _configured_artifacts_path() -> Path | None:
    value = os.getenv("DOCLING_ARTIFACTS_PATH", "").strip()
    return Path(value) if value else None


def _label(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "text").strip().lower().replace("-", "_")


def _call_export(
    item: Any,
    name: str,
    document: Any,
    *,
    preserve_whitespace: bool = False,
) -> str:
    method = getattr(item, name, None)
    if not callable(method):
        return ""
    for args, kwargs in (((document,), {}), ((), {"doc": document}), ((), {})):
        try:
            exported = str(method(*args, **kwargs) or "")
            return exported if preserve_whitespace else exported.strip()
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


def _document_page_height(document: Any, page_number: int) -> float | None:
    pages = getattr(document, "pages", None)
    if isinstance(pages, Mapping):
        page = pages.get(page_number)
    elif isinstance(pages, Sequence) and not isinstance(pages, (str, bytes)):
        try:
            page = pages[page_number - 1]
        except (IndexError, TypeError):
            return None
    else:
        return None
    if page is None:
        return None
    size = getattr(page, "size", None)
    try:
        height = float(getattr(size, "height", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    return height if height > 0 else None


def _provenance(
    item: Any,
    document: Any,
) -> tuple[int, tuple[float, float, float, float]]:
    records = tuple(getattr(item, "prov", ()) or ())
    if not records:
        return 1, (0.0, 0.0, 0.0, 0.0)
    record = records[0]
    page_number = max(1, int(getattr(record, "page_no", 1) or 1))
    bbox = getattr(record, "bbox", None)
    to_top_left = getattr(bbox, "to_top_left_origin", None)
    page_height = _document_page_height(document, page_number)
    if callable(to_top_left) and page_height is not None:
        bbox = to_top_left(page_height=page_height)
    return page_number, _bbox_tuple(bbox)


def _item_text(item: Any, document: Any, block_type: str) -> tuple[str, str, str]:
    raw_text = str(getattr(item, "text", "") or "").strip()
    caption = _call_export(item, "caption_text", document)
    markdown = _call_export(
        item,
        "export_to_markdown",
        document,
        preserve_whitespace=block_type == "table",
    )
    if block_type == "equation" and markdown:
        raw_text = raw_text or markdown
    elif block_type == "table":
        raw_text = markdown or raw_text or caption
    elif block_type in {"figure", "algorithm"}:
        raw_text = raw_text or caption or markdown
    return raw_text, caption, markdown


def _clean_picture_text(value: str) -> str:
    """Remove Docling's unavailable-image diagnostic and inline image payloads."""
    if value.strip() == _PICTURE_PLACEHOLDER:
        return ""
    without_placeholder = _PICTURE_PLACEHOLDER_COMMENT_RE.sub("", value)
    return _DATA_URI_RE.sub("", without_placeholder).strip()


def _get_source_image(item: Any, document: Any) -> tuple[Any | None, str | None]:
    method = getattr(item, "get_image", None)
    if not callable(method):
        return None, None
    for args, kwargs in (((document,), {}), ((), {"doc": document}), ((), {})):
        try:
            image = method(*args, **kwargs)
        except TypeError:
            continue
        except Exception:
            return None, "image_extraction_failed"
        if image is not None:
            return image, None
        return None, None
    return None, "image_extraction_failed"


def _save_visual_asset(
    item: Any,
    document: Any,
    path: Path,
    page_number: int,
    asset_type: str,
    asset_number: int,
) -> tuple[str | None, str | None]:
    image, extraction_error = _get_source_image(item, document)
    if image is None:
        return None, extraction_error
    asset_root = path.parent / f"{path.stem}_assets"
    asset_name = f"page_{page_number:03}_{asset_type}_{asset_number:03}.png"
    target = asset_root / asset_name
    temporary_target: Path | None = None
    try:
        if asset_root.is_symlink():
            return None, "image_write_target_unsafe"
        asset_root.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            return None, "image_write_target_unsafe"
        with tempfile.NamedTemporaryFile(
            dir=asset_root,
            prefix=f".{asset_name}.",
            suffix=".png",
            delete=False,
        ) as temporary_file:
            temporary_target = Path(temporary_file.name)
        try:
            image.save(temporary_target, format="PNG")
        except TypeError:
            image.save(temporary_target)
        if not temporary_target.is_file():
            return None, "image_write_failed"
        os.replace(temporary_target, target)
    except Exception:
        try:
            if temporary_target is not None and temporary_target.is_file():
                temporary_target.unlink()
        except OSError:
            pass
        return None, "image_write_failed"
    return (asset_name, None) if target.is_file() else (None, "image_write_failed")


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
    if hasattr(options, "generate_picture_images"):
        options.generate_picture_images = True
    if hasattr(options, "generate_page_images"):
        options.generate_page_images = True
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
    figure_counts: Counter[int] = Counter()
    table_counts: Counter[int] = Counter()
    previous_algorithm_title: str | None = None

    for source_items, entry in enumerate(document.iterate_items(), start=1):
        item, level = entry if isinstance(entry, tuple) else (entry, 0)
        source_label = _label(getattr(item, "label", "text"))
        raw_title = str(getattr(item, "text", "") or "").strip()
        title_caption = _call_export(item, "caption_text", document).strip()
        algorithm_title = next(
            (
                value
                for value in (title_caption, raw_title)
                if _ALGORITHM_HEADING_RE.search(value)
            ),
            None,
        ) if source_label in _HEADING_LABELS or source_label == "caption" else None
        if source_label in _DROP_LABELS:
            previous_algorithm_title = algorithm_title
            continue
        block_type = _LABEL_TYPES.get(source_label, "body")
        text, caption, markdown = _item_text(item, document, block_type)
        if block_type == "figure":
            caption = _clean_picture_text(caption)
            text = _clean_picture_text(text) or caption
            markdown = _clean_picture_text(markdown)
        if block_type == "code" and previous_algorithm_title:
            block_type = "algorithm"
            caption = previous_algorithm_title
        if source_label in _HEADING_LABELS:
            block_type = "heading"
        page_number, bbox = _provenance(item, document)
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
        if block_type in {"figure", "table"}:
            if block_type == "figure":
                figure_counts[page_number] += 1
                asset_type = "figure"
                asset_number = figure_counts[page_number]
            else:
                table_counts[page_number] += 1
                asset_type = "table"
                asset_number = table_counts[page_number]
            asset_name, image_error = _save_visual_asset(
                item, document, path, page_number, asset_type, asset_number
            )
            metadata["source_image_available"] = bool(asset_name)
            if asset_name:
                metadata["asset_name"] = asset_name
            if image_error:
                metadata["source_image_error"] = image_error
        if not text and not markdown and not (
            block_type in {"figure", "table"}
            and (metadata.get("asset_name") or metadata.get("source_image_error"))
        ):
            previous_algorithm_title = algorithm_title
            continue
        label_text = caption or text
        if block_type in {"equation", "table", "figure", "algorithm"}:
            metadata["label"] = label_text.splitlines()[0][:160] if label_text else ""
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
        previous_algorithm_title = algorithm_title

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
    if any(
        block.metadata.get("asset_name")
        for blocks in by_page.values()
        for block in blocks
    ):
        manifest["asset_directory"] = f"{path.stem}_assets"
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
