from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
import hashlib
import re

from app.papers.parsing import ParsedPaper, ParsedSection


@dataclass(frozen=True)
class ChunkDraft:
    position: int
    content: str
    content_hash: str
    token_count: int
    section_id: str | None = None
    section_path: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    char_start: int | None = None
    char_end: int | None = None

    def embedding_text(self, paper_title: str) -> str:
        section = self.section_path or self.section_id or "Document"
        return f"Paper: {paper_title}\nSection: {section}\n\n{self.content}"


def _draft(position: int, content: str) -> ChunkDraft:
    clean = content.strip()
    return ChunkDraft(
        position=position,
        content=clean,
        content_hash=hashlib.sha256(clean.encode("utf-8")).hexdigest(),
        token_count=max(1, len(clean) // 4),
    )


def chunk_text(text: str, max_chars: int = 900, overlap_chars: int = 120) -> list[ChunkDraft]:
    if max_chars < 50:
        max_chars = max(1, max_chars)
    overlap_chars = max(0, min(overlap_chars, max_chars - 1))
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    contents: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            contents.append(current.strip())
        current = ""

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            flush()
            start = 0
            step = max(1, max_chars - overlap_chars)
            while start < len(paragraph):
                piece = paragraph[start : start + max_chars].strip()
                if piece:
                    contents.append(piece)
                if start + max_chars >= len(paragraph):
                    break
                start += step
            continue

        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= max_chars:
            current = candidate
            continue

        previous = current
        flush()
        carry = previous[-overlap_chars:].strip() if overlap_chars else ""
        candidate = f"{carry}\n\n{paragraph}".strip() if carry else paragraph
        current = candidate if len(candidate) <= max_chars else paragraph

    flush()
    return [_draft(position, content) for position, content in enumerate(contents)]


_NON_RETRIEVAL_SECTION_KINDS = {
    "references",
    "acknowledgments",
    "header",
    "footer",
}


@dataclass(frozen=True)
class _TextUnit:
    text: str
    start: int
    end: int
    paragraph_index: int


def _sentence_spans(text: str, base_offset: int, paragraph_index: int) -> list[_TextUnit]:
    units: list[_TextUnit] = []
    pattern = re.compile(r".+?(?:[.!?。！？]+(?:[\"'”’）\]]*)|$)", re.DOTALL)
    for match in pattern.finditer(text):
        raw = match.group(0)
        clean = raw.strip()
        if not clean:
            continue
        leading = len(raw) - len(raw.lstrip())
        start = base_offset + match.start() + leading
        units.append(_TextUnit(clean, start, start + len(clean), paragraph_index))
    return units


def _split_hard(unit: _TextUnit, max_chars: int) -> list[_TextUnit]:
    if len(unit.text) <= max_chars:
        return [unit]
    pieces: list[_TextUnit] = []
    local = 0
    while local < len(unit.text):
        remaining = unit.text[local:]
        if len(remaining) <= max_chars:
            piece = remaining.strip()
            leading = len(remaining) - len(remaining.lstrip())
            if piece:
                start = unit.start + local + leading
                pieces.append(_TextUnit(piece, start, start + len(piece), unit.paragraph_index))
            break
        window = remaining[:max_chars]
        boundaries = [match.end() for match in re.finditer(r"[\s,;:，；：、)]", window)]
        cut = boundaries[-1] if boundaries and boundaries[-1] >= max_chars // 2 else max_chars
        raw_piece = remaining[:cut]
        piece = raw_piece.strip()
        leading = len(raw_piece) - len(raw_piece.lstrip())
        if piece:
            start = unit.start + local + leading
            pieces.append(_TextUnit(piece, start, start + len(piece), unit.paragraph_index))
        local += cut
    return pieces


def _section_units(text: str, max_chars: int) -> list[_TextUnit]:
    units: list[_TextUnit] = []
    for paragraph_index, match in enumerate(re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", text, re.DOTALL)):
        paragraph = match.group(0).strip()
        if not paragraph:
            continue
        leading = len(match.group(0)) - len(match.group(0).lstrip())
        paragraph_start = match.start() + leading
        display_math = paragraph.startswith("$$") and paragraph.endswith("$$")
        if len(paragraph) <= max_chars or display_math:
            units.append(
                _TextUnit(
                    paragraph,
                    paragraph_start,
                    paragraph_start + len(paragraph),
                    paragraph_index,
                )
            )
            continue
        for sentence in _sentence_spans(paragraph, paragraph_start, paragraph_index):
            units.extend(_split_hard(sentence, max_chars))
    if not units and text.strip():
        leading = len(text) - len(text.lstrip())
        fallback = _TextUnit(text.strip(), leading, leading + len(text.strip()), 0)
        units.extend(_split_hard(fallback, max_chars))
    return units


def _join_units(units: list[_TextUnit]) -> str:
    rendered = ""
    previous_paragraph: int | None = None
    for unit in units:
        if rendered:
            rendered += "\n\n" if unit.paragraph_index != previous_paragraph else " "
        rendered += unit.text
        previous_paragraph = unit.paragraph_index
    return rendered


def _overlap_units(units: list[_TextUnit], overlap_chars: int) -> list[_TextUnit]:
    if overlap_chars <= 0:
        return []
    selected: list[_TextUnit] = []
    used = 0
    for unit in reversed(units):
        added = len(unit.text) + (1 if selected else 0)
        if used + added > overlap_chars:
            break
        selected.append(unit)
        used += added
    return list(reversed(selected))


def chunk_sections(
    sections: Sequence[ParsedSection],
    max_chars: int = 900,
    overlap_chars: int = 120,
) -> list[ChunkDraft]:
    """Chunk parsed sections without crossing section boundaries.

    Complete sections remain canonical; chunks are derived retrieval units.
    """
    max_chars = max(1, int(max_chars))
    overlap_chars = max(0, min(int(overlap_chars), max_chars - 1))
    drafts: list[ChunkDraft] = []

    for section in sections:
        kind = str(getattr(section, "kind", "document") or "document").lower()
        if kind in _NON_RETRIEVAL_SECTION_KINDS:
            continue
        text = str(getattr(section, "text", "") or "").strip()
        if not text:
            continue
        units = _section_units(text, max_chars)
        current: list[_TextUnit] = []

        def flush() -> None:
            nonlocal current
            if not current:
                return
            content = _join_units(current).strip()
            if not content:
                current = []
                return
            section_start = int(getattr(section, "char_start", 0) or 0)
            drafts.append(
                ChunkDraft(
                    position=len(drafts),
                    content=content,
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    token_count=max(1, len(content) // 4),
                    section_id=str(getattr(section, "section_id", "document") or "document"),
                    section_path=str(getattr(section, "title", "Document") or "Document"),
                    page_start=int(getattr(section, "page_start", 1) or 1),
                    page_end=int(getattr(section, "page_end", 1) or 1),
                    char_start=section_start + min(unit.start for unit in current),
                    char_end=section_start + max(unit.end for unit in current),
                )
            )
            current = _overlap_units(current, overlap_chars)

        for unit in units:
            candidate = _join_units([*current, unit])
            if current and len(candidate) > max_chars:
                flush()
                candidate = _join_units([*current, unit])
                if current and len(candidate) > max_chars:
                    current = []
            current.append(unit)
        flush()

    return drafts


_ATOMIC_BLOCK_TYPES = {"equation", "table", "figure", "algorithm"}


def _section_for_page(sections: Sequence[ParsedSection], page_number: int) -> ParsedSection | None:
    return next(
        (
            section
            for section in sections
            if int(section.page_start) <= page_number <= int(section.page_end)
            and str(section.kind).lower() not in _NON_RETRIEVAL_SECTION_KINDS
        ),
        None,
    )


def _atomic_block_content(block: object) -> str:
    block_type = str(getattr(block, "block_type", "body") or "body").lower()
    metadata = dict(getattr(block, "metadata", {}) or {})
    label = str(metadata.get("label") or block_type.title())
    caption = str(metadata.get("caption") or "").strip()
    markdown = str(metadata.get("markdown") or "").strip()
    raw_text = str(getattr(block, "text", "") or "").strip()
    header = f"[{block_type.upper()} {label}]"
    parts = [header]
    if caption:
        parts.append(caption)
    if markdown:
        parts.append(markdown)
    elif raw_text:
        parts.append(raw_text)
    return "\n\n".join(dict.fromkeys(part for part in parts if part)).strip()


def chunk_multimodal(
    parsed: ParsedPaper,
    max_chars: int = 900,
    overlap_chars: int = 120,
) -> list[ChunkDraft]:
    """Chunk prose by section while preserving typed paper blocks atomically."""
    atomic: list[tuple[int, int, ChunkDraft]] = []
    removable_by_section: dict[str, list[str]] = {}
    for page in parsed.pages:
        for block in page.blocks:
            block_type = str(block.block_type or "body").lower()
            if block_type not in _ATOMIC_BLOCK_TYPES:
                continue
            section = _section_for_page(parsed.sections, int(page.page_number))
            if section is None:
                continue
            content = _atomic_block_content(block)
            if not content:
                continue
            metadata = dict(block.metadata or {})
            label = str(metadata.get("label") or block_type.title())
            atomic.append(
                (
                    int(page.page_number),
                    int(block.reading_order),
                    ChunkDraft(
                        position=0,
                        content=content,
                        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        token_count=max(1, len(content) // 4),
                        section_id=section.section_id,
                        section_path=f"{section.title} > {label}",
                        page_start=int(page.page_number),
                        page_end=int(page.page_number),
                        char_start=None,
                        char_end=None,
                    ),
                )
            )
            removable_by_section.setdefault(section.section_id, []).append(block.text)

    prose_sections: list[ParsedSection] = []
    for section in parsed.sections:
        text = section.text
        for value in removable_by_section.get(section.section_id, []):
            if value:
                text = text.replace(value, "", 1)
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
        if text:
            prose_sections.append(replace(section, text=text))

    prose = chunk_sections(prose_sections, max_chars, overlap_chars)
    ordered: list[tuple[int, int, ChunkDraft]] = [
        (int(chunk.page_start or 1), 10_000 + index, chunk)
        for index, chunk in enumerate(prose)
    ]
    ordered.extend(atomic)
    ordered.sort(key=lambda item: (item[0], item[1]))
    return [replace(chunk, position=index) for index, (_, _, chunk) in enumerate(ordered)]
