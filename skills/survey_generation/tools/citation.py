from __future__ import annotations

import re
from difflib import get_close_matches
from typing import Any


class CitationGuard:
    pattern = re.compile(r"\[(paper:[^\]]+)\]")

    def extract_ids(self, text: str) -> list[str]:
        return self.pattern.findall(text)

    def verify_citations(self, text: str, paper_pool: list[dict[str, Any]]) -> dict[str, Any]:
        found_ids = self.extract_ids(text)
        valid_ids = {paper["paper_id"] for paper in paper_pool}
        hallucinated = [paper_id for paper_id in found_ids if paper_id not in valid_ids]
        missing = [paper_id for paper_id in valid_ids if paper_id not in found_ids]
        suggestions = {
            bad_id: get_close_matches(bad_id, valid_ids, n=3)
            for bad_id in hallucinated
        }
        coverage = 0.0 if not valid_ids else (len(valid_ids - set(missing)) / len(valid_ids))
        return {
            "is_valid": not hallucinated,
            "found_ids": found_ids,
            "hallucinated_ids": hallucinated,
            "missing_reference_ids": missing,
            "coverage": round(coverage, 4),
            "suggestions": suggestions,
        }

