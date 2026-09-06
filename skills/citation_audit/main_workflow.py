from __future__ import annotations

from typing import Any, AsyncIterator


async def run_citation_audit_workflow(state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    from app.services.citation_evidence import bind_paragraphs
    from skills.survey_generation.tools.citation import CitationGuard

    text = str(state.get("text") or state.get("markdown") or "")
    papers = list(state.get("papers") or [])
    if not text.strip():
        raise ValueError("text is required")
    audit = CitationGuard().verify_citations(text, papers)
    evidence = bind_paragraphs(text, papers)
    audit.update(evidence)
    audit["is_valid"] = audit["is_valid"] and evidence["evidence_located"]
    yield {"event": "skill_result", "phase": "citation_audit", "message": "引用审计完成", "percent": 100,
           "payload": {"tenant_id": state["tenant_id"], "user_id": state["user_id"], "citation_audit": audit}}
