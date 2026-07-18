from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence


_CAPTION_RE = re.compile(
    r"^(?P<label>(?:fig(?:ure)?|table|algorithm|scheme)\.?\s+[A-Z]?\d+[a-z]?)"
    r"(?P<separator>\s*[.:：—-]\s*|\s+)"
    r"(?P<caption>.+)$",
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
    caption = re.sub(r"\s+", " ", match.group("caption")).strip()
    separator = match.group("separator")
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
    try:
        finder = page.find_tables()
    except Exception:
        return []
    candidates: list[dict[str, Any]] = []
    for table in getattr(finder, "tables", []) or []:
        try:
            rows = table.extract() or []
            candidates.append({"bbox": _rect_tuple(table.bbox), "rows": rows})
        except Exception:
            continue
    return candidates


def _render_crop(page: Any, bbox: tuple[float, float, float, float], output: Path) -> None:
    import fitz

    output.parent.mkdir(parents=True, exist_ok=True)
    matrix = fitz.Matrix(200 / 72.0, 200 / 72.0)
    pixmap = page.get_pixmap(matrix=matrix, clip=fitz.Rect(*bbox), alpha=False)
    pixmap.save(str(output))


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

    visual_rects = _visual_rects(page)
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

        if kind == "table" and tables:
            nearest = min(
                tables,
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
        elif kind == "algorithm":
            member_rects = [caption_bbox]
            lines = []
            previous_bottom = caption_bbox[3]
            for index in range(caption_index + 1, len(blocks)):
                candidate = blocks[index]
                if caption_parts(str(getattr(candidate, "text", ""))):
                    break
                candidate_bbox = _rect_tuple(candidate.bbox)
                gap = candidate_bbox[1] - previous_bottom
                if gap > 38 or candidate_bbox[1] > caption_bbox[1] + 240:
                    break
                if candidate_bbox[1] >= caption_bbox[1] - 2:
                    member_rects.append(candidate_bbox)
                    lines.append(str(candidate.text).strip())
                    consumed.add(index)
                    previous_bottom = max(previous_bottom, candidate_bbox[3])
            bbox = _clip(_union(member_rects), page.rect)
            if lines:
                markdown = "```text\n" + "\n".join(line for line in lines if line) + "\n```"
                source_text += "\n" + "\n".join(line for line in lines if line)
                quality_status = "usable"
            else:
                quality_reasons.append("algorithm_body_unavailable")
        else:
            nearby = [
                rect for rect in visual_rects
                if rect[3] <= caption_bbox[1] + 4 and caption_bbox[1] - rect[3] <= 260
            ]
            if nearby:
                bbox = _clip(_union([*nearby, caption_bbox]), page.rect)
                quality_status = "usable"
            else:
                previous_bottom = max(
                    (
                        _rect_tuple(block.bbox)[3]
                        for block in blocks[:caption_index]
                        if _rect_tuple(block.bbox)[3] < caption_bbox[1]
                    ),
                    default=max(0.0, caption_bbox[1] - 180.0),
                )
                if caption_bbox[1] - previous_bottom < 40:
                    previous_bottom = max(0.0, caption_bbox[1] - 180.0)
                bbox = _clip((caption_bbox[0], previous_bottom, caption_bbox[2], caption_bbox[3]), page.rect)
                quality_reasons.append("visual_geometry_weak")

        if bbox is None or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            quality_status = "rejected"
            quality_reasons.append("invalid_crop")
            asset_name = ""
        else:
            asset_name = f"page_{page_number:03d}_{_safe_label(label)}.png"
            try:
                _render_crop(page, bbox, asset_root / asset_name)
            except Exception:
                asset_name = ""
                quality_status = "rejected"
                quality_reasons.append("crop_render_failed")

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
                    "source_bbox": list(bbox or caption_bbox),
                },
            }
        )
    return output
