from __future__ import annotations

import hashlib
import re
from typing import Any

from app.retrieval.bm25 import search_tokens


def evidence_for_paper(paper: dict[str, Any], query: str, limit: int = 3) -> list[dict[str, Any]]:
    field = next((name for name in ("snippet", "full_text", "abstract") if str(paper.get(name) or "").strip()), "abstract")
    content = str(paper.get(field) or "")
    terms = set(search_tokens(query))
    candidates = []
    for match in re.finditer(r"[^\n]+", content):
        text = match.group(0).strip()
        if not text:
            continue
        # Bound long PDF paragraphs while preserving exact source offsets.
        for start in range(0, len(text), 1600):
            quote = text[start:start + 1600]
            offset = content.find(quote, match.start())
            score = len(terms.intersection(search_tokens(quote)))
            candidates.append((score, offset, quote))
    candidates.sort(key=lambda row: (-row[0], row[1]))
    return [{
        "paper_id": paper["paper_id"], "field": field, "quote": quote,
        "start": offset, "end": offset + len(quote),
        "chunk_id": paper.get("chunk_id"), "page_start": paper.get("page_start"),
        "content_version": paper.get("content_version", 0),
        "sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        "scope": "abstract" if field == "abstract" else "source_text",
    } for _, offset, quote in candidates[:limit]]


def bind_paragraphs(text: str, paper_pool: list[dict[str, Any]]) -> dict[str, Any]:
    papers = {paper["paper_id"]: paper for paper in paper_pool}
    bindings = []
    unsupported = []
    for index, paragraph in enumerate(re.split(r"\n\s*\n", text.strip()), start=1):
        paragraph = "\n".join(line for line in paragraph.splitlines() if not re.match(r"^\s{0,3}#{1,6}\s", line)).strip()
        if not paragraph:
            continue
        source_ids = list(dict.fromkeys(re.findall(r"\[(paper:[^\]]+)\]", paragraph)))
        evidence = [item for source_id in source_ids if source_id in papers
                    for item in evidence_for_paper(papers[source_id], paragraph, limit=1)]
        missing = [source_id for source_id in source_ids if not any(item["paper_id"] == source_id for item in evidence)]
        status = "located" if source_ids and not missing else "missing_evidence"
        if status != "located":
            unsupported.append(index)
        bindings.append({"paragraph_id": index, "text": paragraph, "source_ids": source_ids,
                         "evidence": evidence, "status": status})
    return {"bindings": bindings, "unsupported_paragraphs": unsupported,
            "evidence_located": bool(bindings) and not unsupported,
            "semantic_verification": "not_performed"}


def validate_semantic_review(payload: dict[str, Any], bindings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A supported verdict must quote an exact substring of supplied evidence."""
    reviews = payload.get("paragraphs")
    if not isinstance(reviews, list):
        raise ValueError("quality reviewer must return paragraphs")
    by_id = {str(item.get("paragraph_id")): item for item in reviews if isinstance(item, dict)}
    if len(reviews) != len(bindings) or set(by_id) != {str(item["paragraph_id"]) for item in bindings}:
        raise ValueError("quality review must cover every paragraph exactly once")
    result = []
    for binding in bindings:
        review = by_id.get(str(binding["paragraph_id"]), {})
        quote = str(review.get("quote") or "").strip()
        paper_id = str(review.get("paper_id") or "")
        supported = binding["status"] == "located" and review.get("verdict") == "supported" and len(quote) >= 8 and any(
            item["paper_id"] == paper_id and quote in item["quote"] for item in binding["evidence"]
        )
        result.append({"paragraph_id": binding["paragraph_id"], "passed": supported,
                       "paper_id": paper_id, "quote": quote if supported else "",
                       "reason": str(review.get("reason") or "Evidence did not support the paragraph")[:1000]})
    return result
