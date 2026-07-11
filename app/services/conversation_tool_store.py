from __future__ import annotations

import uuid
from typing import Any

from app.schemas import UserContext
from app.services import mysql_store


class ConversationToolCallStore:
    def create(
        self,
        user: UserContext,
        conversation_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        status: str = "planned",
    ) -> dict[str, Any]:
        call = {
            "call_id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "tenant_id": user.tenant_id,
            "user_id": user.user_id,
            "tool_name": tool_name,
            "arguments": arguments,
            "status": status,
            "result": {},
            "error": "",
        }
        mysql_store.execute(
            """
            INSERT INTO scholar_conversation_tool_calls
                (call_id, conversation_id, tenant_id, user_id, tool_name,
                 arguments_json, status, result_json, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call["call_id"],
                conversation_id,
                user.tenant_id,
                user.user_id,
                tool_name,
                mysql_store.encode_json(arguments),
                status,
                mysql_store.encode_json({}),
                "",
            ),
        )
        return call

    def get(self, user: UserContext, conversation_id: str, call_id: str) -> dict[str, Any] | None:
        row = mysql_store.fetch_one(
            """
            SELECT * FROM scholar_conversation_tool_calls
            WHERE tenant_id=? AND user_id=? AND conversation_id=? AND call_id=?
            LIMIT 1
            """,
            (user.tenant_id, user.user_id, conversation_id, call_id),
        )
        return self._from_row(row) if row else None

    def latest_pending(self, user: UserContext, conversation_id: str) -> dict[str, Any] | None:
        row = mysql_store.fetch_one(
            """
            SELECT * FROM scholar_conversation_tool_calls
            WHERE tenant_id=? AND user_id=? AND conversation_id=?
              AND status='awaiting_confirmation'
            ORDER BY created_at DESC LIMIT 1
            """,
            (user.tenant_id, user.user_id, conversation_id),
        )
        return self._from_row(row) if row else None

    def has_succeeded(
        self, user: UserContext, conversation_id: str, tool_name: str
    ) -> bool:
        row = mysql_store.fetch_one(
            """
            SELECT call_id FROM scholar_conversation_tool_calls
            WHERE tenant_id=? AND user_id=? AND conversation_id=?
              AND tool_name=? AND status='succeeded'
            LIMIT 1
            """,
            (user.tenant_id, user.user_id, conversation_id, tool_name),
        )
        return row is not None

    def update(
        self,
        user: UserContext,
        conversation_id: str,
        call_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> dict[str, Any] | None:
        mysql_store.execute(
            """
            UPDATE scholar_conversation_tool_calls
            SET status=?, result_json=?, error=?, updated_at=datetime('now')
            WHERE tenant_id=? AND user_id=? AND conversation_id=? AND call_id=?
            """,
            (
                status,
                mysql_store.encode_json(result or {}),
                error,
                user.tenant_id,
                user.user_id,
                conversation_id,
                call_id,
            ),
        )
        return self.get(user, conversation_id, call_id)

    @staticmethod
    def _from_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "call_id": row["call_id"],
            "conversation_id": row["conversation_id"],
            "tenant_id": row["tenant_id"],
            "user_id": row["user_id"],
            "tool_name": row["tool_name"],
            "arguments": mysql_store.decode_json(row.get("arguments_json"), {}),
            "status": row["status"],
            "result": mysql_store.decode_json(row.get("result_json"), {}),
            "error": row.get("error") or "",
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
        }


conversation_tool_call_store = ConversationToolCallStore()
