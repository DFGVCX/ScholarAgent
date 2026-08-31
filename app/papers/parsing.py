from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
import hashlib
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from app.papers.assets import inventory_from_pages
from app.papers.formulas import (
    contains_invalid_controls,
    extract_numbered_formula,
    recover_formula,
)


STRUCTURED_PARSER_NAME = "structure_aware_v1"
STRUCTURED_PARSER_VERSION = "1"
FORMULA_AWARE_PARSER_NAME = "formula_aware_v2"
FORMULA_AWARE_PARSER_VERSION = "2"
MULTIMODAL_PARSER_NAME = "multimodal_aware_v3"
MULTIMODAL_PARSER_VERSION = "3"
HIERARCHICAL_PARSER_NAME = "scholar_hierarchical_v4"
HIERARCHICAL_PARSER_VERSION = "4"
LEGACY_PARSER_NAME = "legacy_fixed"
LEGACY_PARSER_VERSION = "1"


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sanitize_text(value: str) -> str:
    """Keep extracted text valid for PostgreSQL while preserving word boundaries."""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", value or "")


def _normalize_space(value: str) -> str:
    return re.sub(r"[ \t\f\v]+", " ", _sanitize_text(value)).strip()


@dataclass(frozen=True)
class ParsedBlock:
    page_number: int
    block_type: str
    text: str
    bbox: tuple[float, float, float, float]
    reading_order: int
    font_size: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "block_type": self.block_type,
            "text": self.text,
            "bbox": list(self.bbox),
            "reading_order": self.reading_order,
            "font_size": self.font_size,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ParsedPage:
    page_number: int
    text: str
    text_hash: str
    searchable_chars: int
    extraction_method: str
    quality_status: str
    blocks: tuple[ParsedBlock, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "text": self.text,
            "text_hash": self.text_hash,
            "searchable_chars": self.searchable_chars,
            "extraction_method": self.extraction_method,
            "quality_status": self.quality_status,
            "blocks": [block.to_dict() for block in self.blocks],
        }


@dataclass(frozen=True)
class ParsedSection:
    section_id: str
    index: int
    kind: str
    title: str
    page_start: int
    page_end: int
    text: str
    char_start: int
    char_end: int
    text_hash: str
    parent_section_id: str | None = None
    section_path: str | None = None
    heading_level: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "index": self.index,
            "kind": self.kind,
            "title": self.title,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "text": self.text,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "char_count": len(self.text),
            "text_hash": self.text_hash,
            "parent_section_id": self.parent_section_id,
            "section_path": self.section_path or self.title,
            "heading_level": self.heading_level,
        }


@dataclass(frozen=True)
class ParsedPaper:
    full_text: str
    pages: tuple[ParsedPage, ...]
    sections: tuple[ParsedSection, ...]
    metadata: Mapping[str, Any]
    manifest: Mapping[str, Any]
    status: str
    quality_score: float
    warnings: tuple[str, ...] = ()
    error: str | None = None

    def to_manifest(self) -> dict[str, Any]:
        return {
            **dict(self.manifest),
            "asset_inventory": inventory_from_pages(self.pages),
            "status": self.status,
            "quality_score": self.quality_score,
            "warnings": list(self.warnings),
            "error": self.error,
            "metadata": dict(self.metadata),
            "sections": [
                {
                    key: value
                    for key, value in section.to_dict().items()
                    if key != "text"
                }
                for section in self.sections
            ],
        }


@dataclass(frozen=True)
class _RawPage:
    page_number: int
    width: float
    height: float
    blocks: tuple[ParsedBlock, ...]


_SECTION_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("abstract", ("abstract", "摘要")),
    ("introduction", ("introduction", "background", "引言", "绪论")),
    ("related_work", ("related work", "literature review", "相关工作", "文献综述")),
    ("method", ("method", "methods", "methodology", "approach", "proposed method", "方法")),
    ("data", ("data", "dataset", "datasets", "materials", "数据")),
    ("experiment", ("experiment", "experiments", "experimental results", "evaluation", "results", "实验", "结果")),
    ("discussion", ("discussion", "讨论")),
    ("conclusion", ("conclusion", "conclusions", "结论")),
    ("acknowledgments", ("acknowledgment", "acknowledgments", "acknowledgement", "acknowledgements", "致谢")),
    ("references", ("references", "bibliography", "参考文献")),
    ("appendix", ("appendix", "supplementary material", "附录")),
)


def _join_lines(lines: Sequence[str]) -> str:
    output = ""
    for raw in lines:
        line = _normalize_space(raw)
        if not line:
            continue
        if output.endswith("-") and re.match(r"^[a-z]", line):
            output = output[:-1] + line
        elif output:
            output += " " + line
        else:
            output = line
    return output.strip()


def _page_blocks(page: Any, page_number: int) -> tuple[ParsedBlock, ...]:
    payload = page.get_text("dict", sort=True)
    blocks: list[ParsedBlock] = []
    for raw_block in payload.get("blocks", []):
        if raw_block.get("type") != 0:
            continue
        line_texts: list[str] = []
        font_sizes: list[float] = []
        for line in raw_block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(str(span.get("text") or "") for span in spans)
            if _normalize_space(text):
                line_texts.append(text)
            font_sizes.extend(float(span.get("size") or 0.0) for span in spans)
        text = _join_lines(line_texts)
        if not text:
            continue
        bbox_value = raw_block.get("bbox") or (0.0, 0.0, 0.0, 0.0)
        bbox = tuple(float(value) for value in bbox_value[:4])
        blocks.append(
            ParsedBlock(
                page_number=page_number,
                block_type="body",
                text=text,
                bbox=bbox,  # type: ignore[arg-type]
                reading_order=len(blocks),
                font_size=max(font_sizes, default=0.0),
            )
        )
    return tuple(blocks)


def _margin_key(text: str) -> str:
    normalized = _normalize_space(text).lower()
    normalized = re.sub(r"\d+", "#", normalized)
    return normalized


def _repeated_margin_keys(pages: Sequence[_RawPage]) -> set[str]:
    occurrences: Counter[str] = Counter()
    for page in pages:
        keys: set[str] = set()
        for block in page.blocks:
            _, y0, _, y1 = block.bbox
            in_margin = y1 <= page.height * 0.12 or y0 >= page.height * 0.88
            if in_margin and len(block.text) <= 160:
                key = _margin_key(block.text)
                if key:
                    keys.add(key)
        occurrences.update(keys)
    threshold = max(2, math.ceil(len(pages) * 0.5))
    return {key for key, count in occurrences.items() if count >= threshold}


def _ordered_body_blocks(page: _RawPage, repeated: set[str]) -> tuple[ParsedBlock, ...]:
    body: list[ParsedBlock] = []
    for block in page.blocks:
        _, y0, _, y1 = block.bbox
        in_margin = y1 <= page.height * 0.12 or y0 >= page.height * 0.88
        if in_margin and _margin_key(block.text) in repeated:
            continue
        body.append(replace(block, text=_sanitize_text(block.text)))
    if not body:
        return ()

    narrow = [block for block in body if (block.bbox[2] - block.bbox[0]) < page.width * 0.65]
    first_narrow_y = min((block.bbox[1] for block in narrow), default=page.height)
    midpoint = page.width / 2.0

    def key(block: ParsedBlock) -> tuple[float, ...]:
        x0, y0, x1, _ = block.bbox
        if x1 - x0 >= page.width * 0.65:
            band = 0.0 if y0 <= first_narrow_y else 2.0
            return band, y0, x0
        column = 0.0 if (x0 + x1) / 2.0 < midpoint else 1.0
        return 1.0, column, y0, x0

    ordered = sorted(body, key=key)
    return tuple(replace(block, reading_order=index) for index, block in enumerate(ordered))


_NUMBERED_EQUATION_RE = re.compile(
    r"(?<![A-Za-z0-9_])\((?P<label>\d{1,3})\)\s*[,.;:]?\s*$"
)


def _pypdf_page_texts(path: Path) -> tuple[str, ...]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return tuple(_sanitize_text(str(page.extract_text() or "")) for page in reader.pages)
    except Exception:
        return ()


def _formula_fragment(block: ParsedBlock) -> bool:
    text = block.text.strip()
    if not text or len(text) > 180:
        return False
    if contains_invalid_controls(text) or re.search(
        r"[=∑∏∇⊙·∗×‖∼≈≤≥<>]|\\(?:sum|frac|min|max|nabla)", text
    ):
        return True
    tokens = text.split()
    return bool(tokens) and len(tokens) <= 6 and all(
        len(token) <= 8
        and re.fullmatch(r"[A-Za-z0-9_{}()[\],.+*/\-α-ωΑ-Ω]+", token)
        for token in tokens
    )


def _same_formula_column(
    candidate: ParsedBlock,
    label: ParsedBlock,
    page_width: float,
) -> bool:
    midpoint = page_width / 2.0
    label_center = (label.bbox[0] + label.bbox[2]) / 2.0
    candidate_center = (candidate.bbox[0] + candidate.bbox[2]) / 2.0
    return (label_center < midpoint) == (candidate_center < midpoint)


def _formula_aware_blocks(
    blocks: Sequence[ParsedBlock],
    fallback_page_text: str,
    page_width: float = 612.0,
) -> tuple[tuple[ParsedBlock, ...], list[dict[str, Any]]]:
    """Merge numbered display-equation fragments and retain recovery provenance."""
    replacements: dict[int, tuple[ParsedBlock, dict[str, Any]]] = {}
    consumed: set[int] = set()
    equations: list[dict[str, Any]] = []
    label_occurrences: Counter[str] = Counter()
    for index, block in enumerate(blocks):
        match = _NUMBERED_EQUATION_RE.search(block.text)
        if not match or index in consumed:
            continue
        label_x0, label_y0, label_x1, label_y1 = block.bbox
        group_indices = [
            candidate_index
            for candidate_index, candidate in enumerate(blocks)
            if candidate_index not in consumed
            and candidate.page_number == block.page_number
            and (candidate_index == index or _formula_fragment(candidate))
            and _same_formula_column(candidate, block, page_width)
            and candidate.bbox[1] <= label_y1 + 4
            and candidate.bbox[3] >= label_y0 - 8
            and candidate.bbox[0] >= label_x0 - 180
            and candidate.bbox[2] <= label_x1 + 24
        ]
        if index not in group_indices:
            group_indices.append(index)
        group = [blocks[candidate_index] for candidate_index in group_indices]
        if not any(_formula_fragment(part) or "=" in part.text for part in group):
            continue
        raw_text = "\n".join(
            part.text for part in sorted(group, key=lambda item: (item.bbox[1], item.bbox[0]))
        )
        label = match.group("label")
        fallback = extract_numbered_formula(
            fallback_page_text,
            label,
            occurrence=label_occurrences[label],
        )
        label_occurrences[label] += 1
        bbox = (
            min(part.bbox[0] for part in group),
            min(part.bbox[1] for part in group),
            max(part.bbox[2] for part in group),
            max(part.bbox[3] for part in group),
        )
        candidate = recover_formula(
            raw_text=raw_text,
            fallback_text=fallback,
            label=label,
            page_number=block.page_number,
            bbox=bbox,
        )
        insertion_index = min(group_indices)
        record = candidate.to_dict()
        equation_block = ParsedBlock(
            page_number=block.page_number,
            block_type="equation",
            text=candidate.markdown,
            bbox=bbox,
            reading_order=insertion_index,
            font_size=max((part.font_size for part in group), default=block.font_size),
            metadata=record,
        )
        replacements[insertion_index] = (equation_block, record)
        consumed.update(group_indices)

    output: list[ParsedBlock] = []
    for index, block in enumerate(blocks):
        replacement = replacements.get(index)
        if replacement is not None:
            equation_block, record = replacement
            output.append(equation_block)
            equations.append(record)
        elif index not in consumed:
            output.append(block)

    return (
        tuple(replace(block, reading_order=order) for order, block in enumerate(output)),
        equations,
    )


def _matching_equation_record(
    records: Sequence[dict[str, Any]],
    block: ParsedBlock,
) -> dict[str, Any] | None:
    """Match an equation manifest record without collapsing repeated labels.

    Equation numbers are not guaranteed to be unique on a page (appendices,
    extraction errors, and multi-column layouts can repeat them), while their
    source bounding boxes identify the actual occurrence.
    """
    for record in records:
        if record is block.metadata:
            return record

    metadata = dict(block.metadata or {})
    label = str(metadata.get("label") or "")
    candidates = [record for record in records if str(record.get("label") or "") == label]
    if len(candidates) == 1:
        return candidates[0]

    target_bbox = metadata.get("bbox") or block.bbox
    try:
        target = tuple(float(value) for value in target_bbox)
    except (TypeError, ValueError):
        return None
    if len(target) != 4:
        return None
    for record in candidates:
        try:
            candidate = tuple(float(value) for value in record.get("bbox") or ())
        except (TypeError, ValueError):
            continue
        if len(candidate) == 4 and all(
            abs(left - right) <= 0.01 for left, right in zip(candidate, target)
        ):
            return record
    return None


def _heading_kind(block: ParsedBlock, median_font: float) -> str | None:
    value = _normalize_space(block.text)
    if not value or len(value) > 140:
        return None
    normalized = value.lower().rstrip(".:：")
    normalized = re.sub(r"^(?:\d+(?:\.\d+)*|[ivxlcdm]+)[\s.、:：-]+", "", normalized).strip()
    for kind, aliases in _SECTION_ALIASES:
        if normalized in aliases:
            return kind
    if block.font_size >= max(12.0, median_font * 1.18):
        for kind, aliases in _SECTION_ALIASES:
            if any(normalized.startswith(alias + " ") for alias in aliases):
                return kind
    return None


def _section_id(kind: str, seen: Counter[str]) -> str:
    seen[kind] += 1
    return kind if seen[kind] == 1 else f"{kind}-{seen[kind]}"


def _build_sections(pages: Sequence[ParsedPage]) -> tuple[ParsedSection, ...]:
    all_fonts = [block.font_size for page in pages for block in page.blocks if block.font_size > 0]
    sorted_fonts = sorted(all_fonts)
    median_font = sorted_fonts[len(sorted_fonts) // 2] if sorted_fonts else 10.0
    seen: Counter[str] = Counter()
    drafts: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def finalize() -> None:
        nonlocal current
        if current is None:
            return
        text = "\n\n".join(current.pop("paragraphs")).strip()
        if text:
            current["text"] = text
            drafts.append(current)
        current = None

    for page in pages:
        for block in page.blocks:
            kind = _heading_kind(block, median_font)
            if kind:
                finalize()
                current = {
                    "section_id": _section_id(kind, seen),
                    "kind": kind,
                    "title": block.text,
                    "page_start": page.page_number,
                    "page_end": page.page_number,
                    "paragraphs": [],
                }
                continue
            if current is None:
                current = {
                    "section_id": _section_id("preamble", seen),
                    "kind": "preamble",
                    "title": "Preamble",
                    "page_start": page.page_number,
                    "page_end": page.page_number,
                    "paragraphs": [],
                }
            current["page_end"] = page.page_number
            current["paragraphs"].append(block.text)
    finalize()

    sections: list[ParsedSection] = []
    cursor = 0
    for index, draft in enumerate(drafts):
        prefix = "" if draft["kind"] == "preamble" else f"{draft['title']}\n\n"
        rendered = prefix + draft["text"]
        start = cursor
        end = start + len(rendered)
        sections.append(
            ParsedSection(
                section_id=draft["section_id"],
                index=index,
                kind=draft["kind"],
                title=draft["title"],
                page_start=draft["page_start"],
                page_end=draft["page_end"],
                text=draft["text"],
                char_start=start,
                char_end=end,
                text_hash=_hash_text(draft["text"]),
            )
        )
        cursor = end + 2
    return tuple(sections)


def _render_sections(sections: Sequence[ParsedSection]) -> str:
    rendered: list[str] = []
    for section in sections:
        if section.kind == "preamble":
            rendered.append(section.text)
        else:
            rendered.append(f"{section.title}\n\n{section.text}".strip())
    return "\n\n".join(part for part in rendered if part).strip()


_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE)
_ARXIV_RE = re.compile(r"(?:arxiv\s*:\s*)?(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE)
_CODE_URL_RE = re.compile(r"https?://(?:www\.)?(?:github\.com|gitlab\.com)/[^\s<>\])},;]+", re.IGNORECASE)
_CAPTION_RE = re.compile(r"^(?P<label>(?:fig(?:ure)?|table|algorithm)\s+[a-z]?\d+)\s*[.:：-]?\s*(?P<caption>.+)$", re.IGNORECASE)


def _document_metadata(full_text: str, pdf_metadata: Mapping[str, Any]) -> dict[str, Any]:
    doi = _DOI_RE.search(full_text)
    arxiv = _ARXIV_RE.search(full_text)
    code_urls = sorted({match.rstrip(".") for match in _CODE_URL_RE.findall(full_text)})
    cjk = len(re.findall(r"[\u4e00-\u9fff]", full_text))
    language = "zh" if cjk > max(20, len(full_text) * 0.15) else "en"
    return {
        "title_candidate": _normalize_space(str(pdf_metadata.get("title") or "")),
        "doi": doi.group(0).lower().rstrip(".") if doi else None,
        "arxiv_id": arxiv.group(1).lower() if arxiv else None,
        "code_urls": code_urls,
        "project_urls": code_urls,
        "language": language,
        "pdf_metadata": {str(key): value for key, value in pdf_metadata.items() if value},
    }


def _captions(pages: Sequence[ParsedPage]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in pages:
        for block in page.blocks:
            match = _CAPTION_RE.match(block.text)
            if match:
                items.append(
                    {
                        "page_number": page.page_number,
                        "label": match.group("label"),
                        "caption": match.group("caption"),
                    }
                )
    return items


def _failed(parser_name: str, parser_version: str, error: Exception | str) -> ParsedPaper:
    message = _normalize_space(str(error))[:1000] or "unknown PDF parsing error"
    return ParsedPaper(
        full_text="",
        pages=(),
        sections=(),
        metadata={},
        manifest={
            "parser": {"name": parser_name, "version": parser_version},
            "coverage": {"total_pages": 0, "pages_extracted": 0, "text_truncated": False},
        },
        status="failed",
        quality_score=0.0,
        warnings=("pdf_parse_failed",),
        error=message,
    )


def _parse_layout_pdf(
    path: Path,
    *,
    parser_name: str,
    parser_version: str,
    formula_aware: bool,
    visual_aware: bool = False,
) -> ParsedPaper:
    try:
        import fitz

        document = fitz.open(path)
    except Exception as exc:
        return _failed(parser_name, parser_version, exc)

    try:
        fallback_pages = _pypdf_page_texts(path) if formula_aware else ()
        raw_pages = tuple(
            _RawPage(
                page_number=index + 1,
                width=float(page.rect.width),
                height=float(page.rect.height),
                blocks=_page_blocks(page, index + 1),
            )
            for index, page in enumerate(document)
        )
        repeated = _repeated_margin_keys(raw_pages)
        pages: list[ParsedPage] = []
        equations: list[dict[str, Any]] = []
        visual_blocks: list[dict[str, Any]] = []
        asset_root = path.parent / f"{path.stem}_assets"
        removed_margins: set[str] = set()
        for raw_page in raw_pages:
            body_blocks = _ordered_body_blocks(raw_page, repeated)
            page_equations: list[dict[str, Any]] = []
            if formula_aware:
                fallback_text = (
                    fallback_pages[raw_page.page_number - 1]
                    if raw_page.page_number <= len(fallback_pages)
                    else ""
                )
                body_blocks, page_equations = _formula_aware_blocks(
                    body_blocks,
                    fallback_text,
                    raw_page.width,
                )
            if visual_aware:
                from app.papers.visuals import extract_visual_candidates, render_source_crop

                source_page = document[raw_page.page_number - 1]
                equation_blocks: list[ParsedBlock] = []
                for equation_block in body_blocks:
                    if equation_block.block_type != "equation":
                        equation_blocks.append(equation_block)
                        continue
                    metadata = dict(equation_block.metadata)
                    if metadata.get("confidence") != "high":
                        label = str(metadata.get("label", "equation"))
                        asset_name = (
                            f"page_{raw_page.page_number:03d}_equation_{label}.png"
                        )
                        try:
                            crop_bbox = render_source_crop(
                                source_page,
                                equation_block.bbox,
                                asset_root / asset_name,
                            )
                            metadata.update(
                                {
                                    "asset_name": asset_name,
                                    "source_bbox": list(crop_bbox),
                                }
                            )
                            record = _matching_equation_record(page_equations, equation_block)
                            if record is not None:
                                record.update(metadata)
                        except Exception:
                            metadata["asset_name"] = ""
                    equation_blocks.append(replace(equation_block, metadata=metadata))
                body_blocks = tuple(equation_blocks)
                candidates = extract_visual_candidates(
                    source_page,
                    raw_page.page_number,
                    body_blocks,
                    asset_root,
                )
                consumed = {
                    index
                    for candidate in candidates
                    for index in candidate.pop("consumed_indices", [])
                }
                retained_blocks = [
                    block for index, block in enumerate(body_blocks) if index not in consumed
                ]
                for candidate in candidates:
                    visual_block = ParsedBlock(
                        page_number=raw_page.page_number,
                        block_type=str(candidate["block_type"]),
                        text=str(candidate["text"]),
                        bbox=tuple(candidate["bbox"]),
                        reading_order=int(candidate["reading_order"]),
                        font_size=float(candidate["font_size"]),
                        metadata=dict(candidate["metadata"]),
                    )
                    retained_blocks.append(visual_block)
                    visual_blocks.append(visual_block.to_dict())
                body_blocks = tuple(
                    replace(block, reading_order=order)
                    for order, block in enumerate(
                        sorted(retained_blocks, key=lambda item: item.reading_order)
                    )
                )
            equations.extend(page_equations)
            retained = {id(block) for block in body_blocks}
            for block in raw_page.blocks:
                if id(block) not in retained and _margin_key(block.text) in repeated:
                    removed_margins.add(block.text)
            page_text = "\n\n".join(block.text for block in body_blocks).strip()
            searchable_chars = len(re.sub(r"\s+", "", page_text))
            pages.append(
                ParsedPage(
                    page_number=raw_page.page_number,
                    text=page_text,
                    text_hash=_hash_text(page_text),
                    searchable_chars=searchable_chars,
                    extraction_method=(
                        "pymupdf_layout+pypdf_formula_recovery"
                        if formula_aware
                        else "pymupdf_layout"
                    ),
                    quality_status="usable" if searchable_chars >= 40 else "low_text",
                    blocks=body_blocks,
                )
            )
        sections = _build_sections(pages)
        full_text = _render_sections(sections)
        total_chars = len(re.sub(r"\s+", "", full_text))
        low_text_pages = sum(page.searchable_chars < 40 for page in pages)
        insufficient = total_chars < 100 or (len(pages) > 1 and low_text_pages * 2 >= len(pages))
        status = "needs_ocr" if insufficient else "ready"
        warnings = ("searchable_text_insufficient",) if insufficient else ()
        usable_ratio = (len(pages) - low_text_pages) / max(1, len(pages))
        quality_score = round(usable_ratio * min(1.0, total_chars / 1000.0), 6)
        metadata = _document_metadata(full_text, document.metadata or {})
        manifest = {
            "parser": {"name": parser_name, "version": parser_version},
            "coverage": {
                "total_pages": len(document),
                "pages_extracted": len(pages),
                "low_text_pages": low_text_pages,
                "text_truncated": False,
            },
            "language": metadata["language"],
            "text_hash": _hash_text(full_text),
            "removed_repeated_margins": sorted(removed_margins),
            "captions": _captions(pages),
        }
        if formula_aware:
            manifest["equations"] = equations
        if visual_aware:
            manifest["visual_blocks"] = visual_blocks
            manifest["asset_directory"] = asset_root.name
        return ParsedPaper(
            full_text=full_text if status == "ready" else "",
            pages=tuple(pages),
            sections=sections if status == "ready" else (),
            metadata=metadata,
            manifest=manifest,
            status=status,
            quality_score=quality_score,
            warnings=warnings,
        )
    except Exception as exc:
        return _failed(parser_name, parser_version, exc)
    finally:
        document.close()


def parse_pdf(path: Path) -> ParsedPaper:
    return _parse_layout_pdf(
        path,
        parser_name=STRUCTURED_PARSER_NAME,
        parser_version=STRUCTURED_PARSER_VERSION,
        formula_aware=False,
    )


def parse_pdf_formula_aware(path: Path) -> ParsedPaper:
    return _parse_layout_pdf(
        path,
        parser_name=FORMULA_AWARE_PARSER_NAME,
        parser_version=FORMULA_AWARE_PARSER_VERSION,
        formula_aware=True,
    )


def parse_pdf_multimodal(path: Path) -> ParsedPaper:
    return _parse_layout_pdf(
        path,
        parser_name=MULTIMODAL_PARSER_NAME,
        parser_version=MULTIMODAL_PARSER_VERSION,
        formula_aware=True,
        visual_aware=True,
    )


def _sanitize_fallback_reason(value: object) -> str:
    detail = _sanitize_text(str(value or "Docling failed"))
    detail = re.sub(r"(?i)(?:[a-z]:\\|/)(?:[^\s:]+[/\\])+[^\s:]+", "<path>", detail)
    detail = re.sub(r"(?i)(?:sk-|api[_-]?key[=: ]+)[a-z0-9._-]{4,}", "<secret>", detail)
    detail = re.sub(r"\s+", " ", detail).strip()
    return detail[:500]


def parse_pdf_hierarchical(path: Path) -> ParsedPaper:
    """Use Docling first and retain the proven v3 parser as a safe fallback."""
    from app.papers.docling_adapter import parse_docling_pdf

    try:
        parsed = parse_docling_pdf(path)
        if parsed.status == "ready":
            return parsed
        reason = _sanitize_fallback_reason("; ".join(parsed.warnings) or parsed.status)
    except SystemExit as exc:
        reason = _sanitize_fallback_reason(f"Docling exited with status {exc.code}")
    except Exception as exc:
        reason = _sanitize_fallback_reason(str(exc) or exc.__class__.__name__)

    fallback = parse_pdf_multimodal(path)
    manifest = {
        **dict(fallback.manifest),
        "parser": {
            "name": MULTIMODAL_PARSER_NAME,
            "version": MULTIMODAL_PARSER_VERSION,
            "engine": "pymupdf_multimodal",
        },
        "requested_parser": HIERARCHICAL_PARSER_NAME,
        "actual_parser": MULTIMODAL_PARSER_NAME,
        "fallback_reason": reason,
        "fallback": {
            "from": "docling",
            "to": MULTIMODAL_PARSER_NAME,
            "reason": reason,
        },
    }
    return replace(
        fallback,
        manifest=manifest,
        warnings=tuple(dict.fromkeys((*fallback.warnings, "parser_fallback"))),
    )


def parse_pdf_legacy(path: Path) -> ParsedPaper:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        page_texts = [_sanitize_text(str(page.extract_text() or "")).strip() for page in reader.pages]
        pages = tuple(
            ParsedPage(
                page_number=index + 1,
                text=text,
                text_hash=_hash_text(text),
                searchable_chars=len(re.sub(r"\s+", "", text)),
                extraction_method="pypdf",
                quality_status="usable" if len(re.sub(r"\s+", "", text)) >= 40 else "low_text",
                blocks=(
                    ParsedBlock(index + 1, "body", text, (0.0, 0.0, 0.0, 0.0), 0),
                ) if text else (),
            )
            for index, text in enumerate(page_texts)
        )
        raw_full_text = "\n".join(text for text in page_texts if text).strip()
        full_text = raw_full_text[:50000]
        text_truncated = len(raw_full_text) > len(full_text)
        total_chars = len(re.sub(r"\s+", "", full_text))
        status = "ready" if total_chars >= 40 else "needs_ocr"
        warnings = () if status == "ready" else ("searchable_text_insufficient",)
        section = ParsedSection(
            section_id="document",
            index=0,
            kind="document",
            title="Document",
            page_start=1,
            page_end=max(1, len(pages)),
            text=full_text,
            char_start=0,
            char_end=len(full_text),
            text_hash=_hash_text(full_text),
        )
        pdf_metadata = dict(getattr(reader, "metadata", {}) or {})
        return ParsedPaper(
            full_text=full_text if status == "ready" else "",
            pages=pages,
            sections=(section,) if status == "ready" else (),
            metadata=_document_metadata(full_text, pdf_metadata),
            manifest={
                "parser": {"name": LEGACY_PARSER_NAME, "version": LEGACY_PARSER_VERSION},
                "coverage": {
                    "total_pages": len(pages),
                    "pages_extracted": len(pages),
                    "text_truncated": text_truncated,
                },
                "text_hash": _hash_text(full_text),
            },
            status=status,
            quality_score=round(min(1.0, total_chars / 1000.0), 6),
            warnings=warnings,
        )
    except Exception as exc:
        return _failed(LEGACY_PARSER_NAME, LEGACY_PARSER_VERSION, exc)
