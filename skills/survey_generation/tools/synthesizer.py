from __future__ import annotations

from typing import Any


class OutlineSynthesizer:
    def synthesize(self, topic: str, chunks: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        return [
            {
                "section_id": "background",
                "title": "Research Background and Motivation",
                "paper_ids": [paper["paper_id"] for paper in chunks[0][:3]] if chunks else [],
            },
            {
                "section_id": "methods",
                "title": "Method Families and Technical Evolution",
                "paper_ids": [paper["paper_id"] for chunk in chunks for paper in chunk[:2]][:5],
            },
            {
                "section_id": "evaluation",
                "title": "Evaluation, Risks, and Open Problems",
                "paper_ids": [paper["paper_id"] for chunk in chunks for paper in chunk[-2:]][:5],
            },
            {
                "section_id": "future",
                "title": "Future Directions",
                "paper_ids": [paper["paper_id"] for chunk in chunks for paper in chunk[:1]][:4],
            },
        ]

    def to_markdown(self, outline: list[dict[str, Any]], topic: str) -> str:
        lines = [f"# Survey Outline: {topic}"]
        for index, section in enumerate(outline, start=1):
            lines.append(f"## {index}. {section['title']}")
        return "\n".join(lines)

