from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


@dataclass(frozen=True)
class ChunkDraft:
    position: int
    content: str
    content_hash: str
    token_count: int


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
