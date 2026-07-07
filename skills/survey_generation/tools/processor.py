from __future__ import annotations

from typing import Any


class LiteratureProcessor:
    def estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    def chunk_literature(
        self,
        papers: list[dict[str, Any]],
        max_tokens: int = 4000,
    ) -> list[list[dict[str, Any]]]:
        chunks: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_tokens = 0
        for paper in papers:
            text = f"{paper.get('title', '')}\n{paper.get('abstract') or paper.get('abs', '')}"
            tokens = self.estimate_tokens(text)
            if current and current_tokens + tokens > max_tokens:
                chunks.append(current)
                current = []
                current_tokens = 0
            current.append(paper)
            current_tokens += tokens
        if current:
            chunks.append(current)
        return chunks

