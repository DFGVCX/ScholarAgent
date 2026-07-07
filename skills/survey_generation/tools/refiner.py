from __future__ import annotations


class LCERefiner:
    def merge_sections(self, topic: str, sections: list[dict], references: list[str]) -> str:
        lines = [f"# Survey on {topic}", ""]
        for section in sections:
            lines.append(section["content"].strip())
            lines.append("")
        lines.append("## References")
        for index, reference in enumerate(references, start=1):
            lines.append(f"{index}. {reference}")
        return "\n".join(lines).strip() + "\n"

