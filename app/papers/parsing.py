from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import hashlib
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


STRUCTURED_PARSER_NAME = "structure_aware_v1"
STRUCTURED_PARSER_VERSION = "1"
LEGACY_PARSER_NAME = "legacy_fixed"
LEGACY_PARSER_VERSION = "1"


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sanitize_text(value: str) -> str:
    """Keep extracted text valid for PostgreSQL while preserving word boundaries."""
    return (value or "").replace("\x00", " ")


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "block_type": self.block_type,
            "text": self.text,
            "bbox": list(self.bbox),
            "reading_order": self.reading_order,
            "font_size": self.font_size,
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


def parse_pdf(path: Path) -> ParsedPaper:
    try:
        import fitz

        document = fitz.open(path)
    except Exception as exc:
        return _failed(STRUCTURED_PARSER_NAME, STRUCTURED_PARSER_VERSION, exc)

    try:
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
        removed_margins: set[str] = set()
        for raw_page in raw_pages:
            body_blocks = _ordered_body_blocks(raw_page, repeated)
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
                    extraction_method="pymupdf_layout",
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
            "parser": {"name": STRUCTURED_PARSER_NAME, "version": STRUCTURED_PARSER_VERSION},
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
        return _failed(STRUCTURED_PARSER_NAME, STRUCTURED_PARSER_VERSION, exc)
    finally:
        document.close()


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
