from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence


_CAPTION_RE = re.compile(
    r"^(?P<label>(?:fig(?:ure)?|table|algorithm|scheme)\.?\s+(?:[A-Z]?\d+[a-z]?|[IVXLCDM]+))"
    r"(?:(?P<separator>\s*[.:：—-]\s*|\s+)(?P<caption>.+))?$",
    re.IGNORECASE,
)
_INLINE_REFERENCE_STARTS = {
    "show",
    "shows",
    "summarize",
    "summarizes",
    "compare",
    "compares",
    "report",
    "reports",
    "illustrate",
    "illustrates",
    "depict",
    "depicts",
}


def caption_parts(value: str) -> tuple[str, str, str] | None:
    """Return normalized (kind, label, caption) for a real caption-looking line."""
    match = _CAPTION_RE.match((value or "").strip())
    if not match:
        return None
    label = re.sub(r"\s+", " ", match.group("label")).strip().rstrip(".")
    caption = re.sub(r"\s+", " ", match.group("caption") or "").strip()
    separator = match.group("separator") or ""
    first_word_match = re.match(r"([A-Za-z]+)", caption)
    first_word = first_word_match.group(1).lower() if first_word_match else ""
    if separator.isspace() and first_word in _INLINE_REFERENCE_STARTS:
        return None
    lowered = label.lower()
    kind = "table" if lowered.startswith("table") else (
        "algorithm" if lowered.startswith("algorithm") else "figure"
    )
    return kind, label, caption


def caption_kind(value: str) -> str | None:
    parts = caption_parts(value)
    return parts[0] if parts else None


def _clean_cell(value: Any) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
    return cleaned.replace("|", r"\|")


def rows_to_markdown(rows: Sequence[Sequence[Any]]) -> str:
    normalized = [[_clean_cell(cell) for cell in row] for row in rows if row]
    if not normalized:
        return ""
    width = max(len(row) for row in normalized)
    normalized = [row + [""] * (width - len(row)) for row in normalized]

    def render(row: Sequence[str]) -> str:
        return "| " + " | ".join(row) + " |"

    return "\n".join(
        [render(normalized[0]), render(["---"] * width), *(render(row) for row in normalized[1:])]
    )


def asset_root_for_pdf(path: Path) -> Path:
    return path.parent / f"{path.stem}_assets"


def _safe_label(label: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", label).strip("_").lower()
    return value or "visual"


def _rect_tuple(value: Any) -> tuple[float, float, float, float]:
    return tuple(float(part) for part in tuple(value)[:4])  # type: ignore[return-value]


def _union(rects: Sequence[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        min(rect[0] for rect in rects),
        min(rect[1] for rect in rects),
        max(rect[2] for rect in rects),
        max(rect[3] for rect in rects),
    )


def _clip(rect: tuple[float, float, float, float], page_rect: Any) -> tuple[float, float, float, float]:
    return (
        max(float(page_rect.x0), rect[0] - 8.0),
        max(float(page_rect.y0), rect[1] - 8.0),
        min(float(page_rect.x1), rect[2] + 8.0),
        min(float(page_rect.y1), rect[3] + 8.0),
    )


def _visual_rects(page: Any) -> list[tuple[float, float, float, float]]:
    rects: list[tuple[float, float, float, float]] = []
    try:
        for drawing in page.get_drawings():
            rect = drawing.get("rect")
            if rect is not None and float(rect.width) >= 10 and float(rect.height) >= 2:
                rects.append(_rect_tuple(rect))
    except Exception:
        pass
    try:
        for image in page.get_images(full=True):
            for rect in page.get_image_rects(int(image[0])):
                if not rect.is_empty:
                    rects.append(_rect_tuple(rect))
    except Exception:
        pass
    return rects


def _table_candidates(page: Any) -> list[dict[str, Any]]:
    finders: list[tuple[Any, bool]] = []
    try:
        default_finder = page.find_tables()
        finders.append((default_finder, False))
        if not (getattr(default_finder, "tables", []) or []):
            finders.append(
                (
                    page.find_tables(vertical_strategy="text", horizontal_strategy="text"),
                    True,
                )
            )
    except Exception:
        pass
    candidates: list[dict[str, Any]] = []
    page_width = max(float(page.rect.x1) - float(page.rect.x0), 1.0)
    page_height = max(float(page.rect.y1) - float(page.rect.y0), 1.0)
    page_area = page_width * page_height
    for finder, is_text_fallback in finders:
        for table in getattr(finder, "tables", []) or []:
            try:
                bbox = _rect_tuple(table.bbox)
                area_ratio = max(bbox[2] - bbox[0], 0.0) * max(bbox[3] - bbox[1], 0.0) / page_area
                if is_text_fallback and area_ratio > 0.65:
                    continue
                rows = table.extract() or []
                candidates.append({"bbox": bbox, "rows": rows})
            except Exception:
                continue
        if candidates:
            break
    return candidates


def _caption_column(
    page_rect: Any,
    caption_bbox: tuple[float, float, float, float],
) -> tuple[float, float]:
    page_x0 = float(page_rect.x0)
    page_x1 = float(page_rect.x1)
    midpoint = (page_x0 + page_x1) / 2.0
    page_width = max(page_x1 - page_x0, 1.0)
    if caption_bbox[2] - caption_bbox[0] >= page_width * 0.55:
        return page_x0, page_x1
    if (caption_bbox[0] + caption_bbox[2]) / 2.0 < midpoint:
        return page_x0, midpoint
    return midpoint, page_x1


def _table_matches_caption(
    page_rect: Any,
    table: Mapping[str, Any],
    caption_bbox: tuple[float, float, float, float],
) -> bool:
    """Reject plausible-looking tables that actually belong to another column."""
    bbox = _rect_tuple(table["bbox"])
    column_x0, column_x1 = _caption_column(page_rect, caption_bbox)
    page_width = max(float(page_rect.x1) - float(page_rect.x0), 1.0)
    caption_is_full_width = column_x1 - column_x0 >= page_width * 0.9
    if caption_is_full_width:
        return True
    table_center = (bbox[0] + bbox[2]) / 2.0
    return column_x0 <= table_center <= column_x1 and bbox[2] - bbox[0] <= page_width * 0.6


def _table_rule_crop(
    page: Any,
    caption_bbox: tuple[float, float, float, float],
    maximum_bottom: float,
) -> tuple[float, float, float, float] | None:
    """Infer an original-table crop from long horizontal rules without inventing cells."""
    column_x0, column_x1 = _caption_column(page.rect, caption_bbox)
    column_width = max(column_x1 - column_x0, 1.0)
    rules = []
    try:
        drawing_rects = [
            _rect_tuple(drawing["rect"])
            for drawing in page.get_drawings()
            if drawing.get("rect") is not None
        ]
    except Exception:
        drawing_rects = []
    for rect in drawing_rects:
        center_x = (rect[0] + rect[2]) / 2.0
        width = rect[2] - rect[0]
        if not (column_x0 <= center_x <= column_x1):
            continue
        if width < column_width * 0.45:
            continue
        if rect[1] < caption_bbox[3] or rect[3] > maximum_bottom:
            continue
        rules.append(rect)
    if len(rules) < 2:
        return None
    return _clip(_union([caption_bbox, *rules]), page.rect)


def _caption_backed_table_crop(
    page: Any,
    caption_bbox: tuple[float, float, float, float],
    blocks: Sequence[Any],
    consumed: set[int],
) -> tuple[float, float, float, float]:
    """Keep a debuggable table image when cell geometry cannot be recovered safely."""
    page_x0 = float(page.rect.x0)
    page_x1 = float(page.rect.x1)
    page_y1 = float(page.rect.y1)
    column_x0, column_x1 = _caption_column(page.rect, caption_bbox)

    crop_bottom = min(page_y1, caption_bbox[1] + 260.0)
    for index, block in enumerate(blocks):
        if index in consumed:
            continue
        block_bbox = _rect_tuple(block.bbox)
        horizontal_overlap = min(column_x1, block_bbox[2]) - max(column_x0, block_bbox[0])
        if horizontal_overlap <= 0 or block_bbox[1] < caption_bbox[3] + 45.0:
            continue
        text = str(getattr(block, "text", "") or "").strip()
        if len(text) >= 60 or caption_parts(text):
            crop_bottom = min(crop_bottom, block_bbox[1] - 4.0)
            break
    crop_bottom = max(crop_bottom, caption_bbox[3] + 80.0)
    return _clip((column_x0, caption_bbox[1], column_x1, crop_bottom), page.rect)


def _trimmed_crop_bbox(
    page: Any,
    bbox: tuple[float, float, float, float],
    *,
    dpi: int = 200,
) -> tuple[float, float, float, float]:
    import fitz
    from PIL import Image

    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pixmap = page.get_pixmap(matrix=matrix, clip=fitz.Rect(*bbox), alpha=False)
    image: Image.Image = pixmap.pil_image().convert("L")
    mask = image.point(lambda value: 255 if value < 248 else 0)
    content = mask.getbbox()
    if not content:
        return bbox
    padding = 12
    left = max(0, content[0] - padding)
    top = max(0, content[1] - padding)
    right = min(image.width, content[2] + padding)
    bottom = min(image.height, content[3] + padding)
    scale = dpi / 72.0
    trimmed = (
        bbox[0] + left / scale,
        bbox[1] + top / scale,
        bbox[0] + right / scale,
        bbox[1] + bottom / scale,
    )
    if trimmed[2] - trimmed[0] < 12 or trimmed[3] - trimmed[1] < 12:
        return bbox
    return trimmed


def _render_crop(
    page: Any,
    bbox: tuple[float, float, float, float],
    output: Path,
) -> tuple[float, float, float, float]:
    import fitz

    output.parent.mkdir(parents=True, exist_ok=True)
    bbox = _trimmed_crop_bbox(page, bbox)
    matrix = fitz.Matrix(200 / 72.0, 200 / 72.0)
    pixmap = page.get_pixmap(matrix=matrix, clip=fitz.Rect(*bbox), alpha=False)
    pixmap.save(str(output))
    return bbox


def render_source_crop(
    page: Any,
    bbox: tuple[float, float, float, float],
    output: Path,
) -> tuple[float, float, float, float]:
    """Render a tightly trimmed source crop for parser debugging."""
    return _render_crop(page, bbox, output)


def _same_column(
    page_rect: Any,
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    first_column = _caption_column(page_rect, first)
    second_center = (second[0] + second[2]) / 2.0
    return first_column[0] <= second_center <= first_column[1]


def _figure_crop(
    page: Any,
    caption_index: int,
    caption_bbox: tuple[float, float, float, float],
    captions: Sequence[tuple[int, Any, tuple[str, str, str]]],
    blocks: Sequence[Any],
) -> tuple[float, float, float, float]:
    column_x0, column_x1 = _caption_column(page.rect, caption_bbox)
    previous_caption_bottoms = [
        _rect_tuple(other.bbox)[3]
        for other_index, other, _ in captions
        if other_index != caption_index
        and _rect_tuple(other.bbox)[1] < caption_bbox[1]
        and _same_column(page.rect, caption_bbox, _rect_tuple(other.bbox))
    ]
    if previous_caption_bottoms:
        # Keep a full inter-caption gutter so the previous caption cannot
        # re-enter the raster trim for the next visual block.
        top = max(previous_caption_bottoms) + 16.0
    else:
        previous_structure_bottoms = []
        for index, block in enumerate(blocks):
            if index == caption_index:
                continue
            block_bbox = _rect_tuple(block.bbox)
            text = str(getattr(block, "text", "") or "").strip()
            if block_bbox[3] >= caption_bbox[1] or not _same_column(
                page.rect, caption_bbox, block_bbox
            ):
                continue
            if len(text) >= 80 or float(getattr(block, "font_size", 0.0) or 0.0) >= 12.0:
                previous_structure_bottoms.append(block_bbox[3])
        page_top = float(page.rect.y0)
        top = max(previous_structure_bottoms, default=page_top + 52.0) + 4.0
        if caption_bbox[1] - top < 45.0:
            top = max(page_top + 52.0, caption_bbox[1] - 240.0)
    return (
        max(float(page.rect.x0), column_x0),
        max(float(page.rect.y0), top),
        min(float(page.rect.x1), column_x1),
        min(float(page.rect.y1), caption_bbox[3] + 8.0),
    )


def _looks_like_algorithm_prose(value: str) -> bool:
    text = re.sub(r"\s+", " ", value or "").strip()
    if not text:
        return False
    if re.match(
        r"^(?:input|output|require|ensure|for|while|if|else|return|repeat|until|procedure)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    if re.match(r"^\d+\s+", text):
        return False
    words = re.findall(r"[A-Za-z]{2,}", text)
    return bool(re.match(r"^[a-z]\)\s+[A-Z]", text)) or (
        len(text) >= 90 and len(words) >= 12
    )


def extract_visual_candidates(
    page: Any,
    page_number: int,
    blocks: Sequence[Any],
    asset_root: Path,
) -> list[dict[str, Any]]:
    """Extract caption-backed visual blocks without interpreting image semantics."""
    captions: list[tuple[int, Any, tuple[str, str, str]]] = []
    for index, block in enumerate(blocks):
        parts = caption_parts(str(getattr(block, "text", "")))
        if parts:
            captions.append((index, block, parts))
    if not captions:
        return []

    tables = _table_candidates(page)
    output: list[dict[str, Any]] = []
    for caption_index, caption_block, (kind, label, caption) in captions:
        caption_bbox = _rect_tuple(caption_block.bbox)
        consumed = {caption_index}
        markdown = ""
        source_text = f"{label}. {caption}".strip()
        quality_status = "review"
        quality_reasons: list[str] = []
        bbox: tuple[float, float, float, float] | None = None

        if not caption and caption_index + 1 < len(blocks):
            continuation = blocks[caption_index + 1]
            continuation_bbox = _rect_tuple(continuation.bbox)
            gap = continuation_bbox[1] - caption_bbox[3]
            continuation_text = str(getattr(continuation, "text", "") or "").strip()
            if continuation_text and not caption_parts(continuation_text) and -3 <= gap <= 28:
                caption = re.sub(r"\s+", " ", continuation_text).strip()
                source_text = f"{label}. {caption}".strip()
                consumed.add(caption_index + 1)

        matching_tables = [
            item for item in tables if _table_matches_caption(page.rect, item, caption_bbox)
        ]
        if kind == "table" and matching_tables:
            nearest = min(
                matching_tables,
                key=lambda item: min(
                    abs(item["bbox"][1] - caption_bbox[3]),
                    abs(caption_bbox[1] - item["bbox"][3]),
                ),
            )
            bbox = _clip(_union([caption_bbox, nearest["bbox"]]), page.rect)
            markdown = rows_to_markdown(nearest["rows"])
            quality_status = "usable" if markdown else "review"
            if not markdown:
                quality_reasons.append("table_cells_unavailable")
            for index, block in enumerate(blocks):
                block_bbox = _rect_tuple(block.bbox)
                if block_bbox[1] >= nearest["bbox"][1] - 2 and block_bbox[3] <= nearest["bbox"][3] + 2:
                    consumed.add(index)
        elif kind == "table":
            column_x0, column_x1 = _caption_column(page.rect, caption_bbox)
            later_caption_tops = [
                _rect_tuple(other_block.bbox)[1]
                for other_index, other_block, _ in captions
                if other_index != caption_index
                and _rect_tuple(other_block.bbox)[1] > caption_bbox[1]
                and column_x0
                <= (_rect_tuple(other_block.bbox)[0] + _rect_tuple(other_block.bbox)[2]) / 2.0
                <= column_x1
            ]
            maximum_bottom = min(
                [caption_bbox[1] + 260.0, *later_caption_tops]
            )
            bbox = _table_rule_crop(
                page,
                caption_bbox,
                maximum_bottom,
            ) or _caption_backed_table_crop(page, caption_bbox, blocks, consumed)
            quality_reasons.append("table_cells_unavailable")
        elif kind == "algorithm":
            member_rects = [caption_bbox]
            lines = []
            previous_bottom = caption_bbox[3]
            column_x0, column_x1 = _caption_column(page.rect, caption_bbox)
            for index in range(caption_index + 1, len(blocks)):
                candidate = blocks[index]
                candidate_text = str(getattr(candidate, "text", "") or "").strip()
                candidate_bbox = _rect_tuple(candidate.bbox)
                candidate_center = (candidate_bbox[0] + candidate_bbox[2]) / 2.0
                if not (column_x0 <= candidate_center <= column_x1):
                    continue
                if caption_parts(candidate_text):
                    break
                gap = candidate_bbox[1] - previous_bottom
                if gap > 38 or candidate_bbox[1] > caption_bbox[1] + 240:
                    break
                if lines and _looks_like_algorithm_prose(candidate_text):
                    break
                if candidate_bbox[1] >= caption_bbox[1] - 2:
                    member_rects.append(candidate_bbox)
                    lines.append(candidate_text)
                    consumed.add(index)
                    previous_bottom = max(previous_bottom, candidate_bbox[3])
            bbox = _clip(_union(member_rects), page.rect)
            if lines:
                markdown = "```text\n" + "\n".join(line for line in lines if line) + "\n```"
                source_text += "\n" + "\n".join(line for line in lines if line)
                quality_status = "usable"
            else:
                bbox = _caption_backed_table_crop(page, caption_bbox, blocks, consumed)
                quality_reasons.append("algorithm_body_unavailable")
        else:
            bbox = _figure_crop(page, caption_index, caption_bbox, captions, blocks)
            quality_status = "usable"

        if bbox is None or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            quality_status = "rejected"
            quality_reasons.append("invalid_crop")
            asset_name = ""
        else:
            asset_name = f"page_{page_number:03d}_{_safe_label(label)}.png"
            try:
                bbox = _render_crop(page, bbox, asset_root / asset_name)
            except Exception:
                asset_name = ""
                quality_status = "rejected"
                quality_reasons.append("crop_render_failed")

        structured_content_available = bool(markdown.strip())
        source_image_available = bool(asset_name)
        if quality_status == "usable":
            extraction_confidence = 0.9 if structured_content_available else 0.85
            fallback_mode = "none"
        elif quality_status == "review":
            extraction_confidence = 0.45 if source_image_available else 0.2
            fallback_mode = "source_image" if source_image_available else "caption_only"
        else:
            extraction_confidence = 0.0
            fallback_mode = "source_image" if source_image_available else "caption_only"

        output.append(
            {
                "block_type": kind,
                "text": source_text,
                "bbox": bbox or caption_bbox,
                "reading_order": min(consumed),
                "font_size": float(getattr(caption_block, "font_size", 0.0) or 0.0),
                "consumed_indices": sorted(consumed),
                "metadata": {
                    "label": label,
                    "caption": caption,
                    "markdown": markdown,
                    "asset_name": asset_name,
                    "quality_status": quality_status,
                    "quality_reasons": quality_reasons,
                    "extraction_confidence": extraction_confidence,
                    "structured_content_available": structured_content_available,
                    "source_image_available": source_image_available,
                    "fallback_mode": fallback_mode,
                    "source_bbox": list(bbox or caption_bbox),
                },
            }
        )
    return output
