from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
import re
from threading import RLock
from typing import Any, Sequence

from rank_bm25 import BM25Plus

from app.retrieval.models import RetrievalCandidate


def search_tokens(text: str) -> list[str]:
    """Keep English identifiers and overlapping CJK bigrams in the same index."""
    tokens = re.findall(r"[a-z0-9][a-z0-9_.-]*", text.casefold())
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.extend(run[i:i + 2] for i in range(max(1, len(run) - 1)))
    return tokens


class BM25CapacityExceeded(RuntimeError):
    pass


class BM25IndexCache:
    """Versioned, bounded tenant indexes; no candidate-only IDF approximation."""

    def __init__(self, max_entries: int = 4, max_documents: int = 10000) -> None:
        self.max_entries = max_entries
        self.max_documents = max_documents
        self._indexes: OrderedDict[tuple[Any, ...], tuple[Any, ...]] = OrderedDict()
        self._lock = RLock()

    def get(self, key: tuple[Any, ...]) -> tuple[Any, ...] | None:
        with self._lock:
            value = self._indexes.get(key)
            if value is not None:
                self._indexes.move_to_end(key)
            return value

    def build(
        self, key: tuple[Any, ...], documents: Sequence[RetrievalCandidate], k1: float, b: float
    ) -> tuple[Any, ...]:
        if len(documents) > self.max_documents:
            raise BM25CapacityExceeded(f"BM25 corpus exceeds {self.max_documents} chunks; use a larger search backend")
        tokens = [search_tokens(f"{item.title} {item.content}") or ["__empty__"] for item in documents]
        index = BM25Plus(tokens, k1=k1, b=b) if tokens else None
        value = (index, tuple(documents), tuple(frozenset(row) for row in tokens))
        with self._lock:
            # Remove older versions of the same tenant/user/filter index.
            for old in list(self._indexes):
                if old[:3] == key[:3] and old != key:
                    self._indexes.pop(old)
            self._indexes[key] = value
            self._indexes.move_to_end(key)
            while len(self._indexes) > self.max_entries:
                self._indexes.popitem(last=False)
        return value

    @staticmethod
    def search(index: tuple[Any, ...], query: str, limit: int) -> list[RetrievalCandidate]:
        scorer, documents, document_tokens = index
        tokens = search_tokens(query)
        if scorer is None or not tokens:
            return []
        scores = scorer.get_scores(tokens)
        selected = [i for i, terms in enumerate(document_tokens) if terms.intersection(tokens)]
        selected.sort(key=lambda i: (-float(scores[i]), documents[i].chunk_id))
        return [replace(documents[i], score=float(scores[i])) for i in selected[:limit]]


bm25_indexes = BM25IndexCache()
