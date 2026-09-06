from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence


_BARE_STEP_ACTION = (
    r"(?:[A-Z]|for\b|while\b|if\b|else\b|return\b|repeat\b|until\b|"
    r"[A-Za-z][A-Za-z0-9_]*\s*(?:=|←)|[\u3400-\u9fff])"
)
_STEP_PATTERN = re.compile(
    rf"(?im)(?:^|(?<=[ \t]))\d+[ \t]*(?:[:.)][ \t]+(?=\S)|[ \t]+(?={_BARE_STEP_ACTION}))"
)


def _confidence(metadata: Mapping[str, Any]) -> float | None:
    value = metadata.get("extraction_confidence")
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, parsed))


def _valid_bbox(bbox: Sequence[float] | None) -> bool:
    if not bbox or len(bbox) != 4:
        return False
    try:
        x0, y0, x1, y1 = (float(value) for value in bbox)
    except (TypeError, ValueError):
        return False
    return x1 > x0 and y1 > y0


def _safe_asset_name(value: object) -> str:
    name = str(value or "").strip()
    return name if name and Path(name).name == name and name not in {".", ".."} else ""


def _balanced_math(value: str) -> bool:
    opening = {"{", "(", "["}
    closing = {"}": "{", ")": "(", "]": "["}
    stack: list[str] = []
    escaped = False
    for char in value:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in opening:
            stack.append(char)
        elif char in closing:
            if not stack or stack.pop() != closing[char]:
                return False
    display_delimiters = len(re.findall(r"(?<!\\)\$\$", value))
    return not stack and display_delimiters % 2 == 0


def _markdown_cells(row: str) -> list[str]:
    return re.split(r"(?<!\\)\|", row.strip()[1:-1])


def _table_checks(markdown: str) -> dict[str, Any]:
    rows = [
        line.strip()
        for line in markdown.splitlines()
        if line.strip().startswith("|") and line.strip().endswith("|")
    ]
    widths = [len(_markdown_cells(row)) for row in rows]
    separator = False
    if len(rows) >= 2:
        separator_cells = [cell.strip() for cell in _markdown_cells(rows[1])]
        separator = bool(separator_cells) and all(
            re.fullmatch(r":?-{3,}:?", cell) for cell in separator_cells
        )
    data_rows = max(0, len(rows) - 2) if separator else 0
    return {
        "markdown_grid": len(rows) >= 2 and separator,
        "header_separator": separator,
        "row_count": data_rows,
        "column_count": widths[0] if widths else 0,
        "columns_consistent": bool(widths) and len(set(widths)) == 1,
    }


def _algorithm_step_count(value: str) -> int:
    count = 0
    for match in _STEP_PATTERN.finditer(value):
        following = value[match.end():].lstrip(" \t")
        if re.match(r"(?i)(?:inputs?|outputs?)\s*[:：]", following):
            continue
        count += 1
    return count


def assess_object_quality(
    block_type: str,
    content: str,
    metadata: Mapping[str, Any] | None = None,
    bbox: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Return conservative, source-auditable quality diagnostics for a typed block."""
    kind = str(block_type or "body").strip().lower()
    source = dict(metadata or {})
    raw = str(content or "").strip()
    markdown = str(source.get("markdown") or "").strip()
    diagnostic_text = markdown or raw
    caption = str(source.get("caption") or source.get("label") or "").strip()
    source_image_available = bool(
        source.get("source_image_available") or _safe_asset_name(source.get("asset_name"))
    )
    equation_delimited = kind == "equation" and raw.startswith("$$") and raw.endswith("$$")
    structured_content_available = bool(
        source.get("structured_content_available") or markdown or equation_delimited
    )
    confidence = _confidence(source)
    source_status = str(source.get("quality_status") or "").strip().lower()
    bbox_valid = _valid_bbox(bbox)
    checks: dict[str, Any] = {
        "content_present": bool(diagnostic_text),
        "caption_present": bool(caption),
        "bbox_valid": bbox_valid,
        "structured_content_available": structured_content_available,
        "source_image_available": source_image_available,
    }
    reasons: list[str] = []
    source_reasons = source.get("quality_reasons")
    if isinstance(source_reasons, Sequence) and not isinstance(source_reasons, (str, bytes)):
        reasons.extend(str(reason).strip() for reason in source_reasons if str(reason).strip())
    score = confidence if confidence is not None else 1.0
    if source_status == "rejected":
        reasons.append("source_marked_rejected")
        score = 0.0
    elif source_status == "review":
        reasons.append("source_marked_review")
        score = min(score, 0.6)
    if confidence is not None and confidence < 0.7:
        reasons.append("low_extraction_confidence")

    auditable_source = bool(diagnostic_text) or (
        kind in {"figure", "table"} and (bool(caption) or source_image_available)
    )
    if not auditable_source:
        reasons.append("empty_content")
        return {
            "version": "v1",
            "status": "rejected",
            "score": 0.0,
            "reasons": list(dict.fromkeys(reasons)),
            "checks": checks,
        }

    if not bbox_valid:
        reasons.append("invalid_bbox")
        score = max(0.0, score - 0.1)
    if kind in {"table", "figure", "algorithm"} and not caption:
        reasons.append("missing_caption")
        score = max(0.0, score - 0.1)

    if kind == "table":
        table = _table_checks(diagnostic_text)
        checks.update(table)
        if not table["markdown_grid"]:
            reasons.append("table_markdown_grid_missing")
            score = min(score, 0.5)
        if table["markdown_grid"] and not table["columns_consistent"]:
            reasons.append("table_columns_inconsistent")
            score = min(score, 0.45)
        if table["markdown_grid"] and table["row_count"] < 1:
            reasons.append("table_data_rows_missing")
            score = min(score, 0.55)
    elif kind == "algorithm":
        step_count = _algorithm_step_count(diagnostic_text)
        has_input = bool(re.search(r"(?im)^\s*(?:\d+\s*[:.)]?\s*)?inputs?\s*[:：]", diagnostic_text))
        has_output = bool(re.search(r"(?im)^\s*(?:\d+\s*[:.)]?\s*)?outputs?\s*[:：]", diagnostic_text))
        checks.update(
            {
                "step_count": step_count,
                "has_input": has_input,
                "has_output": has_output,
            }
        )
        if step_count < 2:
            reasons.append("algorithm_steps_incomplete")
            score = min(score, 0.55)
        if not has_input and not has_output:
            reasons.append("algorithm_io_missing")
            score = min(score, 0.65)
    elif kind == "equation":
        balanced = _balanced_math(diagnostic_text)
        checks["delimiters_balanced"] = balanced
        if not structured_content_available:
            reasons.append("equation_structured_text_missing")
            score = min(score, 0.55)
        if not balanced:
            reasons.append("equation_delimiters_unbalanced")
            score = min(score, 0.45)
    elif kind == "figure":
        if not caption and not source_image_available:
            reasons.append("figure_caption_and_image_missing")
            score = min(score, 0.4)
    elif kind == "code":
        fenced = re.fullmatch(
            r"```([^\n`]*)\n([\s\S]*?)\n```",
            diagnostic_text,
        )
        code_body = fenced.group(2) if fenced else diagnostic_text
        checks.update(
            {
                "fenced_code": fenced is not None,
                "language": fenced.group(1).strip() if fenced else "",
                "line_count": len(code_body.splitlines()),
            }
        )

    score = round(max(0.0, min(1.0, score)), 3)
    status = "rejected" if source_status == "rejected" else "usable" if score >= 0.7 else "review"
    return {
        "version": "v1",
        "status": status,
        "score": score,
        "reasons": list(dict.fromkeys(reasons)),
        "checks": checks,
    }
