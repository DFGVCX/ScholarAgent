from __future__ import annotations

from typing import Any, AsyncIterator


async def run_paper_retrieval_workflow(state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    from mcp_server.scholar_mcp.client import ScholarMCPClient

    query = str(state.get("query") or state.get("topic") or "").strip()
    if not query:
        raise ValueError("query is required")
    result = await ScholarMCPClient().call_tool("search_papers", {
        "tenant_id": state["tenant_id"], "user_id": state["user_id"], "query": query,
        "source": state.get("source", "all"), "limit": max(1, min(int(state.get("limit", 50)), 100)),
    })
    if result.get("status") in {"ERROR", "DENIED"}:
        raise RuntimeError(result.get("error") or "Retrieval denied")
    yield {"event": "skill_result", "phase": "paper_retrieval", "message": "论文检索完成", "percent": 100,
           "payload": {**result, "tenant_id": state["tenant_id"], "user_id": state["user_id"]}}
