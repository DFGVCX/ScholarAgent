from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.schemas import UserContext
from app.services import mysql_store


def _default_state() -> dict[str, Any]:
    return {
        "state_version": 0,
        "current_goal": "",
        "active_domain": "general",
        "active_source": "",
        "phase": "idle",
        "pending_action": None,
        "last_route": None,
        "last_successful_tool": "",
        "last_search_query": "",
        "last_error": "",
        "artifacts": [],
        "recent_results": [],
        "updated_at": "",
    }


class ConversationStateService:
    """Versioned working state reduced from user turns and tool events."""

    def __init__(self) -> None:
        self._schema_ready = False

    def ensure_schema(self) -> None:
        self._schema_ready = True

    def get(self, user: UserContext, conversation_id: str) -> dict[str, Any]:
        self.ensure_schema()
        row = mysql_store.fetch_one(
            "SELECT state_version, state_json FROM scholar_conversation_working_state "
            "WHERE tenant_id=? AND user_id=? AND conversation_id=?",
            (user.tenant_id, user.user_id, conversation_id),
        )
        if not row:
            return _default_state()
        state = mysql_store.decode_json(row.get("state_json"), _default_state())
        state["state_version"] = int(row.get("state_version") or state.get("state_version") or 0)
        return state

    def observe_user_message(
        self, user: UserContext, conversation_id: str, content: str
    ) -> dict[str, Any]:
        state = self.get(user, conversation_id)
        state["current_goal"] = content.strip()[:1000]
        state["phase"] = "planning"
        state["last_error"] = ""
        return self._save(user, conversation_id, state)

    def record_route(
        self,
        user: UserContext,
        conversation_id: str,
        *,
        intent: str,
        target: str,
        execution_mode: str,
        reasons: list[str],
        confidence: float,
        planned_steps: list[str] | None = None,
    ) -> dict[str, Any]:
        state = self.get(user, conversation_id)
        state["last_route"] = {
            "intent": intent,
            "target": target,
            "execution_mode": execution_mode,
            "reasons": reasons,
            "confidence": round(max(0.0, min(confidence, 1.0)), 3),
            "planned_steps": planned_steps or [],
        }
        return self._save(user, conversation_id, state)

    def observe_tool(
        self,
        user: UserContext,
        conversation_id: str,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        status: str,
        result: dict[str, Any] | None = None,
        error: str = "",
        call_id: str = "",
    ) -> dict[str, Any]:
        state = self.get(user, conversation_id)
        result = result or {}
        if tool_name == "search_cnki_papers":
            state.update(active_domain="literature", active_source="cnki")
            state["last_search_query"] = str(arguments.get("query") or "")[:500]
            state["phase"] = "selection_ready" if status == "succeeded" else "search_failed"
        elif tool_name == "search_papers":
            state.update(active_domain="literature", active_source=str(arguments.get("source") or "all"))
            state["last_search_query"] = str(arguments.get("query") or "")[:500]
            state["phase"] = "selection_ready" if status == "succeeded" else "search_failed"
        elif tool_name in {"download_cnki_selections", "download_institution_url", "acquire_paper_to_knowledge"}:
            state["phase"] = "awaiting_confirmation" if status == "awaiting_confirmation" else (
                "completed" if status == "succeeded" else "download_failed"
            )
        elif tool_name in {"start_institution_login", "confirm_institution_browser_login"}:
            state.update(active_domain="institutional_access", active_source="cnki")
            state["phase"] = "completed" if status == "succeeded" else "authentication_required"

        if status == "awaiting_confirmation":
            state["pending_action"] = {
                "call_id": call_id,
                "tool_name": tool_name,
                "arguments": self._compact(arguments),
            }
        elif (state.get("pending_action") or {}).get("call_id") == call_id or status in {"succeeded", "failed", "cancelled"}:
            state["pending_action"] = None

        if status == "succeeded":
            state["last_successful_tool"] = tool_name
            state["last_error"] = ""
            state["recent_results"] = self._result_refs(result)
            state["artifacts"] = self._merge_artifacts(state.get("artifacts") or [], result)
        elif status == "failed":
            state["last_error"] = (error or str(result.get("error") or ""))[:1000]
        return self._save(user, conversation_id, state)

    def clear_pending(self, user: UserContext, conversation_id: str) -> dict[str, Any]:
        state = self.get(user, conversation_id)
        state["pending_action"] = None
        state["phase"] = "idle"
        return self._save(user, conversation_id, state)

    def _save(
        self, user: UserContext, conversation_id: str, state: dict[str, Any]
    ) -> dict[str, Any]:
        self.ensure_schema()
        saved = deepcopy(state)
        saved["state_version"] = int(saved.get("state_version") or 0) + 1
        saved["updated_at"] = datetime.now(timezone.utc).isoformat()
        mysql_store.execute(
            "INSERT INTO scholar_conversation_working_state "
            "(conversation_id, tenant_id, user_id, state_version, state_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, "
            "COALESCE((SELECT created_at FROM scholar_conversation_working_state "
            "WHERE tenant_id=? AND user_id=? AND conversation_id=?), datetime('now')), datetime('now')) "
            "ON CONFLICT (tenant_id, user_id, conversation_id) DO UPDATE SET "
            "state_version=EXCLUDED.state_version, state_json=EXCLUDED.state_json, "
            "updated_at=CURRENT_TIMESTAMP",
            (
                conversation_id, user.tenant_id, user.user_id, saved["state_version"],
                json.dumps(saved, ensure_ascii=False),
                user.tenant_id, user.user_id, conversation_id,
            ),
        )
        return saved

    @staticmethod
    def _compact(value: dict[str, Any]) -> dict[str, Any]:
        return {
            key: item for key, item in value.items()
            if key not in {"tenant_id", "user_id", "confirmation_token"}
        }

    @staticmethod
    def _result_refs(result: dict[str, Any]) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        for item in (result.get("items") or [])[:20]:
            if isinstance(item, dict):
                refs.append({
                    key: item.get(key) for key in ("paper_id", "title", "source", "year", "doi")
                    if item.get(key) is not None
                })
        paper = result.get("paper") or {}
        if isinstance(paper, dict) and paper:
            refs.append({
                key: paper.get(key) for key in ("paper_id", "title", "source", "doi")
                if paper.get(key) is not None
            })
        return refs

    @classmethod
    def _merge_artifacts(
        cls, existing: list[dict[str, Any]], result: dict[str, Any]
    ) -> list[dict[str, Any]]:
        merged = list(existing)
        for ref in cls._result_refs(result):
            if not ref.get("paper_id"):
                continue
            merged = [item for item in merged if item.get("paper_id") != ref["paper_id"]]
            merged.append(ref)
        return merged[-30:]


conversation_state_service = ConversationStateService()
