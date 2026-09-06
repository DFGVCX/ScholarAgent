from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator


async def run_citation_formatting_workflow(state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    from skills.survey_generation.tools.formatter import CitationFormatter

    style = str(state.get("citation_style") or "IEEE")
    if style not in {"IEEE", "APA", "GB/T 7714"}:
        raise ValueError("unsupported citation style")
    papers = list(state.get("papers") or [])
    if not papers or any(not item.get("title") for item in papers):
        raise ValueError("papers with titles are required")
    formatter = CitationFormatter()
    references = await asyncio.to_thread(formatter.batch_process, papers, style)
    yield {"event": "skill_result", "phase": "citation_formatting", "message": "引用格式转换完成", "percent": 100,
           "payload": {"tenant_id": state["tenant_id"], "user_id": state["user_id"],
                       "references": references, "formatter_status": formatter.status()}}
