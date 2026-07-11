from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query

from agents.registry import agent_registry
from app.dependencies import AuthError, authenticate_api_key
from app.services import mysql_store


router = APIRouter(prefix="/agents", tags=["agents"])


def _current_user(x_api_key: str | None):
    try:
        return authenticate_api_key(x_api_key)
    except AuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("")
async def list_agents(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> dict[str, Any]:
    _current_user(x_api_key)
    return {"items": [descriptor.__dict__ for descriptor in agent_registry.list()]}


@router.get("/runs")
async def list_agent_runs(
    conversation_id: str = Query(default="", max_length=80),
    task_id: str = Query(default="", max_length=80),
    limit: int = Query(default=50, ge=1, le=200),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    clauses = ["tenant_id=?", "user_id=?"]
    params: list[Any] = [user.tenant_id, user.user_id]
    if conversation_id:
        clauses.append("conversation_id=?")
        params.append(conversation_id)
    if task_id:
        clauses.append("task_id=?")
        params.append(task_id)
    params.append(limit)
    rows = mysql_store.fetch_all(
        "SELECT run_id,parent_run_id,conversation_id,task_id,agent_name,agent_role,execution_mode,"
        "goal,status,depth,result_json,error,started_at,completed_at FROM scholar_agent_runs WHERE "
        + " AND ".join(clauses)
        + " ORDER BY started_at DESC LIMIT ?",
        tuple(params),
    )
    for row in rows:
        row["result"] = mysql_store.decode_json(row.pop("result_json", None), {})
    return {"items": rows}
