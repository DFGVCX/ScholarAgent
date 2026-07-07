from __future__ import annotations


class SurveyEvaluator:
    def evaluate_section(self, section: dict, citation_audit: dict) -> dict:
        score = 90
        findings: list[str] = []
        if not citation_audit.get("is_valid", False):
            score -= 35
            findings.append("section contains unsupported citation ids")
        if len(section.get("content", "")) < 120:
            score -= 10
            findings.append("section is too short")
        return {"score": max(score, 0), "passed": score >= 85, "findings": findings}

