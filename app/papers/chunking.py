from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
import hashlib
import re
from typing import Any, Mapping

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
    chunk_type: str = "prose"
    parent_section_id: str | None = None
    source_block_ids: tuple[str, ...] = ()
    context_before: str = ""
    context_after: str = ""
    embedding_content: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def embedding_text(self, paper_title: str) -> str:
        section = self.section_path or self.section_id or "Document"
        body = self.embedding_content or self.content
        return f"Paper: {paper_title}\nSection: {section}\n\n{body}"


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


def _estimate_tokens(text: str) -> int:
    """Deterministic token estimate suitable for mixed Chinese/English papers."""
    chinese = len(re.findall(r"[\u3400-\u9fff]", text))
    latin = sum(max(1, (len(word) + 3) // 4) for word in re.findall(r"[A-Za-z0-9_]+", text))
    symbols = len(re.findall(r"[^\s\w\u3400-\u9fff]", text)) // 3
    return max(1, chinese + latin + symbols)


def _source_block_id(block: object) -> str:
    metadata = dict(getattr(block, "metadata", {}) or {})
    return str(
        metadata.get("block_id")
        or f"p{int(getattr(block, 'page_number', 1))}-b{int(getattr(block, 'reading_order', 0))}"
    )


def _neighbor_context(blocks: Sequence[object], index: int) -> tuple[str, str]:
    before = ""
    after = ""
    for candidate in reversed(blocks[:index]):
        if str(getattr(candidate, "block_type", "body")).lower() == "body":
            before = str(getattr(candidate, "text", "") or "").strip()
            if before:
                break
    for candidate in blocks[index + 1 :]:
        if str(getattr(candidate, "block_type", "body")).lower() == "body":
            after = str(getattr(candidate, "text", "") or "").strip()
            if after:
                break
    return before, after


def _table_pieces(metadata: Mapping[str, Any], raw_text: str, max_tokens: int) -> list[str]:
    markdown = str(metadata.get("markdown") or raw_text).strip()
    caption = str(metadata.get("caption") or "").strip()
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    table_lines = [line for line in lines if line.startswith("|") and line.endswith("|")]
    if len(table_lines) < 3:
        content = "\n\n".join(part for part in (caption, markdown) if part)
        return [content] if content else []

    header = table_lines[:2]
    rows = table_lines[2:]
    prefix = "\n\n".join(part for part in (caption, "\n".join(header)) if part)
    pieces: list[str] = []
    current: list[str] = []
    for row in rows:
        candidate = "\n".join([prefix, *current, row])
        if current and _estimate_tokens(candidate) > max_tokens:
            pieces.append("\n".join([prefix, *current]))
            current = []
        current.append(row)
    if current:
        pieces.append("\n".join([prefix, *current]))
    return pieces or [prefix]


def _algorithm_pieces(metadata: Mapping[str, Any], raw_text: str, max_tokens: int) -> list[str]:
    markdown = str(metadata.get("markdown") or raw_text).strip()
    caption = str(metadata.get("caption") or metadata.get("label") or "Algorithm").strip()
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    if not lines:
        return [caption] if caption else []

    steps: list[str] = []
    current = ""
    boundary = re.compile(r"^(?:\d+[.)]|(?:input|output|for|while|if|else|return)\b)", re.IGNORECASE)
    for line in lines:
        if current and boundary.match(line):
            steps.append(current)
            current = line
        else:
            current = f"{current} {line}".strip()
    if current:
        steps.append(current)

    prefix = caption
    pieces: list[str] = []
    selected: list[str] = []
    for step in steps:
        candidate = "\n".join([prefix, *selected, step])
        if selected and _estimate_tokens(candidate) > max_tokens:
            pieces.append("\n".join([prefix, *selected]))
            selected = []
        selected.append(step)
    if selected:
        pieces.append("\n".join([prefix, *selected]))
    return pieces


def _atomic_contents(block: object, target_tokens: int, max_tokens: int) -> list[str]:
    block_type = str(getattr(block, "block_type", "body") or "body").lower()
    metadata = dict(getattr(block, "metadata", {}) or {})
    raw_text = str(getattr(block, "text", "") or "").strip()
    markdown = str(metadata.get("markdown") or "").strip()
    caption = str(metadata.get("caption") or "").strip()
    if block_type == "table":
        return _table_pieces(metadata, raw_text, min(target_tokens, max_tokens))
    if block_type == "equation":
        return [markdown or raw_text]
    if block_type == "algorithm":
        return _algorithm_pieces(metadata, raw_text, max_tokens)
    content = "\n\n".join(dict.fromkeys(part for part in (caption, markdown or raw_text) if part))
    return [content] if content else []


def _section_for_block(sections: Sequence[ParsedSection], block: object) -> ParsedSection | None:
    page_number = int(getattr(block, "page_number", 1) or 1)
    text = str(getattr(block, "text", "") or "")
    candidates = [
        section
        for section in sections
        if int(section.page_start) <= page_number <= int(section.page_end)
        and str(section.kind).lower() not in _NON_RETRIEVAL_SECTION_KINDS
    ]
    return next((section for section in candidates if text and text in section.text), candidates[0] if candidates else None)


def _split_unit_by_tokens(unit: _TextUnit, max_tokens: int) -> list[_TextUnit]:
    if _estimate_tokens(unit.text) <= max_tokens:
        return [unit]
    pieces: list[_TextUnit] = []
    remaining = unit.text
    consumed = 0
    while remaining:
        low, high = 1, len(remaining)
        while low < high:
            middle = (low + high + 1) // 2
            if _estimate_tokens(remaining[:middle]) <= max_tokens:
                low = middle
            else:
                high = middle - 1
        cut = max(1, low)
        if cut < len(remaining):
            candidates = [match.end() for match in re.finditer(r"[\s,;:，；：、]", remaining[:cut])]
            if candidates and candidates[-1] >= cut // 2:
                cut = candidates[-1]
        raw = remaining[:cut]
        clean = raw.strip()
        leading = len(raw) - len(raw.lstrip())
        if clean:
            start = unit.start + consumed + leading
            pieces.append(_TextUnit(clean, start, start + len(clean), unit.paragraph_index))
        consumed += cut
        remaining = remaining[cut:]
    return pieces


def _chunk_section_by_tokens(
    section: ParsedSection,
    target_tokens: int,
    max_tokens: int,
) -> list[ChunkDraft]:
    units = [
        piece
        for unit in _section_units(section.text, max_chars=max_tokens * 4)
        for piece in _split_unit_by_tokens(unit, max_tokens)
    ]
    drafts: list[ChunkDraft] = []
    current: list[_TextUnit] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        content = _join_units(current).strip()
        section_start = int(section.char_start or 0)
        drafts.append(
            ChunkDraft(
                position=0,
                content=content,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                token_count=_estimate_tokens(content),
                section_id=section.section_id,
                section_path=section.title,
                page_start=section.page_start,
                page_end=section.page_end,
                char_start=section_start + min(unit.start for unit in current),
                char_end=section_start + max(unit.end for unit in current),
            )
        )
        current = []

    for unit in units:
        candidate = _join_units([*current, unit])
        if current and _estimate_tokens(candidate) > target_tokens:
            flush()
        current.append(unit)
        if _estimate_tokens(_join_units(current)) >= max_tokens:
            flush()
    flush()
    return drafts


def chunk_hierarchical(
    parsed: ParsedPaper,
    *,
    target_tokens: int = 450,
    max_tokens: int = 800,
) -> list[ChunkDraft]:
    """Create hierarchy-aware prose and typed-object retrieval units.

    Source-faithful ``content`` remains independently inspectable while
    ``embedding_content`` carries local explanatory context for retrieval.
    """
    target_tokens = max(1, min(int(target_tokens), int(max_tokens)))
    max_tokens = max(target_tokens, int(max_tokens))
    atomic: list[tuple[int, int, ChunkDraft]] = []
    removable_by_section: dict[str, list[str]] = {}

    for page in parsed.pages:
        blocks = list(page.blocks)
        for block_index, block in enumerate(blocks):
            block_type = str(block.block_type or "body").lower()
            if block_type not in _ATOMIC_BLOCK_TYPES:
                continue
            section = _section_for_block(parsed.sections, block)
            if section is None:
                continue
            before, after = _neighbor_context(blocks, block_index)
            block_id = _source_block_id(block)
            metadata = dict(block.metadata or {})
            label = str(metadata.get("label") or block_type.title())
            provenance = {
                "page_number": int(page.page_number),
                "bbox": list(block.bbox),
                "reading_order": int(block.reading_order),
                "source_engine": metadata.get("source_engine"),
            }
            for piece_index, content in enumerate(_atomic_contents(block, target_tokens, max_tokens)):
                embedding_parts = [
                    f"{block_type.title()}: {label}",
                    f"Context before: {before}" if before else "",
                    content,
                    f"Context after: {after}" if after else "",
                ]
                embedding_content = "\n\n".join(part for part in embedding_parts if part)
                atomic.append(
                    (
                        int(page.page_number),
                        int(block.reading_order) * 100 + piece_index,
                        ChunkDraft(
                            position=0,
                            content=content,
                            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                            token_count=_estimate_tokens(content),
                            section_id=section.section_id,
                            section_path=f"{section.title} > {label}",
                            page_start=int(page.page_number),
                            page_end=int(page.page_number),
                            chunk_type=block_type,
                            parent_section_id=section.section_id,
                            source_block_ids=(block_id,),
                            context_before=before,
                            context_after=after,
                            embedding_content=embedding_content,
                            metadata={
                                "provenance": provenance,
                                "source_metadata": metadata,
                                "part_index": piece_index,
                            },
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

    prose_drafts = [
        draft
        for section in prose_sections
        for draft in _chunk_section_by_tokens(section, target_tokens, max_tokens)
    ]
    ordered: list[tuple[int, int, ChunkDraft]] = []
    for index, draft in enumerate(prose_drafts):
        source_ids = tuple(
            _source_block_id(block)
            for page in parsed.pages
            if draft.page_start is None or int(page.page_number) >= int(draft.page_start)
            if draft.page_end is None or int(page.page_number) <= int(draft.page_end)
            for block in page.blocks
            if str(block.block_type or "body").lower() == "body" and block.text in draft.content
        )
        ordered.append(
            (
                int(draft.page_start or 1),
                50_000 + index,
                replace(
                    draft,
                    token_count=_estimate_tokens(draft.content),
                    chunk_type="prose",
                    parent_section_id=draft.section_id,
                    source_block_ids=source_ids,
                    embedding_content=draft.content,
                    metadata={"provenance": {"page_start": draft.page_start, "page_end": draft.page_end}},
                ),
            )
        )
    ordered.extend(atomic)
    ordered.sort(key=lambda item: (item[0], item[1]))
    return [replace(chunk, position=index) for index, (_, _, chunk) in enumerate(ordered)]
