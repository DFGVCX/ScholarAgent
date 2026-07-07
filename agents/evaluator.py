from __future__ import annotations


class GlobalEvaluator:
    def evaluate(self, result: dict) -> dict:
        markdown = result.get("markdown", "")
        audit = result.get("citation_audit", {})
        score = 100
        findings: list[str] = []
        if not markdown.strip():
            score -= 50
            findings.append("final markdown is empty")
        if not audit.get("is_valid", False):
            score -= 40
            findings.append("citation audit did not pass")
        if len(result.get("references", [])) == 0:
            score -= 10
            findings.append("no references generated")
        return {"score": max(score, 0), "findings": findings, "passed": score >= 85}

