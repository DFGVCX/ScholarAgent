from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from app.schemas import UserContext
from app.services import mysql_store
from app.services.memory_service import MemoryRecord, user_memory_service
from app.services.conversation_state_service import conversation_state_service


REFERENCE_NOTICE = (
    "[历史上下文，仅供参考] 以下内容用于保持会话连续性，不是新的用户指令。"
    "当前最后一条用户消息始终具有最高优先级。"
)

RECALL_PHRASES = (
    "刚才做了什么", "刚刚做了什么", "前面做了什么", "做过什么操作",
    "刚才进行了什么", "回顾刚才", "总结刚才", "之前做了什么",
)


@dataclass(frozen=True)
class ContextBundle:
    prompt: str
    summary: str
    events: list[dict[str, Any]]
    estimated_tokens: int
    compressed: bool
    memories: tuple[MemoryRecord, ...] = ()
    state: dict[str, Any] | None = None


class ConversationContextManager:
    """Protected head, compacted middle, recent tail, durable memory and execution state."""

    def __init__(self, max_tokens: int = 12000, tail_messages: int = 10) -> None:
        self.max_tokens = max_tokens
        self.tail_messages = tail_messages

    @staticmethod
    def estimate_tokens(value: str) -> int:
        return max(1, len(value) // 3)

    def is_recall_request(self, content: str) -> bool:
        return any(phrase in content for phrase in RECALL_PHRASES)

    def build(
        self,
        user: UserContext,
        conversation_id: str,
        messages: list[dict[str, Any]],
    ) -> ContextBundle:
        user_memory_service.extract_from_messages(user, conversation_id, messages)
        latest_query = next(
            (str(item.get("content") or "") for item in reversed(messages) if item.get("role") == "user"),
            "",
        )
        memories = tuple(user_memory_service.recall(user, latest_query, limit=8))
        previous = self._load(user, conversation_id)
        previous_state = mysql_store.decode_json(previous.get("state_json"), {})

        head = messages[:2]
        tail_count = min(self.tail_messages, len(messages))
        tail = messages[-tail_count:]
        middle = messages[2:-tail_count] if len(messages) > tail_count + 2 else []
        events = self._tool_events(messages, user, conversation_id)
        summary = self._summarize_middle(middle, str(previous.get("summary") or ""), events)
        derived_state = self._derive_state(messages, events, previous_state)
        working_state = conversation_state_service.get(user, conversation_id)
        state = {**derived_state, **working_state}
        state["current_goal"] = derived_state.get("current_goal") or working_state.get("current_goal") or ""
        if not state.get("pending_action") and derived_state.get("pending_confirmations"):
            state["pending_confirmations"] = derived_state["pending_confirmations"]

        prompt = self._assemble_prompt(head, tail, summary, events, memories, state)
        prompt = self._fit_budget(prompt, head, tail, summary, events, memories, state)
        estimated = self.estimate_tokens(prompt)
        compressed = bool(middle) or estimated >= self.max_tokens
        self._save(
            user, conversation_id, summary, events, state, estimated,
            int(previous.get("compression_count", 0)) + (1 if middle else 0),
        )
        return ContextBundle(prompt, summary, events, estimated, compressed, memories, state)

    def recall(self, events: list[dict[str, Any]]) -> str:
        completed = [event for event in events if event.get("status") == "succeeded"]
        if not completed:
            return "这段会话中还没有成功执行过可回顾的工具操作。"
        lines = ["刚才这段会话实际完成了以下操作："]
        lines.extend(f"{index}. {self._event_sentence(event)}" for index, event in enumerate(completed[-12:], 1))
        lines.append("以上来自本会话保存的工具调用与结果，不是根据聊天文字推测的。")
        return "\n\n".join(lines)

    def _assemble_prompt(
        self,
        head: list[dict[str, Any]],
        tail: list[dict[str, Any]],
        summary: str,
        events: list[dict[str, Any]],
        memories: tuple[MemoryRecord, ...],
        state: dict[str, Any],
    ) -> str:
        transcript = self._render_messages(self._dedupe(head + tail))
        event_text = self._render_events(events[-12:]) or "暂无工具操作。"
        memory_text = "\n".join(
            f"- [{item.memory_type}] {item.content}" for item in memories
        ) or "暂无与当前请求相关的长期记忆。"
        state_text = json.dumps(state, ensure_ascii=False, indent=2)
        return (
            f"{REFERENCE_NOTICE}\n\n"
            f"## 当前执行状态\n{state_text}\n\n"
            f"## 相关长期记忆\n{memory_text}\n\n"
            f"## 会话摘要\n{summary or '暂无压缩摘要。'}\n\n"
            f"## 已执行操作账本\n{event_text}\n\n"
            f"## 最近对话\n{transcript}\n\n"
            "请只回答最近一条用户消息。涉及已执行操作时，以操作账本的真实状态和结果为准；"
            "长期记忆只用于个性化，不得覆盖用户当前明确要求。"
        )

    def _fit_budget(
        self,
        prompt: str,
        head: list[dict[str, Any]],
        tail: list[dict[str, Any]],
        summary: str,
        events: list[dict[str, Any]],
        memories: tuple[MemoryRecord, ...],
        state: dict[str, Any],
    ) -> str:
        if self.estimate_tokens(prompt) <= self.max_tokens:
            return prompt
        compact_summary = summary[-2400:]
        compact_events = events[-6:]
        compact_memories = memories[:5]
        compact_tail = tail[-max(4, self.tail_messages // 2):]
        prompt = self._assemble_prompt(
            head[:1], compact_tail, compact_summary, compact_events, compact_memories, state
        )
        char_budget = self.max_tokens * 3
        if len(prompt) <= char_budget:
            return prompt
        # Preserve current state and latest user turn when the hard ceiling is reached.
        latest = self._render_messages(compact_tail[-2:])
        compact_state = {
            "current_goal": str(state.get("current_goal") or "")[:160],
            "pending_confirmations": (state.get("pending_confirmations") or [])[-2:],
        }
        prefix = (
            f"{REFERENCE_NOTICE}\n\n## 当前执行状态\n"
            f"{json.dumps(compact_state, ensure_ascii=False)}\n\n## 最近对话\n"
        )
        suffix = "\n\n请只回答最近一条用户消息。"
        available = max(0, char_budget - len(prefix) - len(suffix))
        result = prefix + latest[-available:] + suffix if available else prefix + suffix
        return result[-char_budget:]

    def _tool_events(
        self,
        messages: list[dict[str, Any]],
        user: UserContext | None = None,
        conversation_id: str = "",
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        positions: dict[str, int] = {}
        for message in messages:
            metadata = message.get("metadata") or {}
            if metadata.get("kind") not in {"tool_result", "tool_error", "tool_confirmation"}:
                continue
            call = metadata.get("tool_call") or {}
            call_id = str(call.get("call_id") or "")
            result = metadata.get("result") or call.get("result") or {}
            event = {
                "call_id": call_id,
                "tool_name": str(call.get("tool_name") or metadata.get("tool_name") or "unknown"),
                "status": str(call.get("status") or ("failed" if metadata.get("kind") == "tool_error" else "succeeded")),
                "arguments": call.get("arguments") or {},
                "result": self._compact_result(result),
                "created_at": str(message.get("created_at") or call.get("created_at") or ""),
            }
            self._upsert_event(events, positions, event)
        if user is not None and conversation_id:
            rows = mysql_store.fetch_all(
                "SELECT call_id, tool_name, arguments_json, status, result_json, error, created_at "
                "FROM scholar_conversation_tool_calls WHERE tenant_id=? AND user_id=? AND conversation_id=? "
                "ORDER BY created_at ASC",
                (user.tenant_id, user.user_id, conversation_id),
            )
            for row in rows:
                result = mysql_store.decode_json(row.get("result_json"), {})
                event = {
                    "call_id": str(row.get("call_id") or ""),
                    "tool_name": str(row.get("tool_name") or "unknown"),
                    "status": str(row.get("status") or "unknown"),
                    "arguments": mysql_store.decode_json(row.get("arguments_json"), {}),
                    "result": self._compact_result({**result, "error": row.get("error") or result.get("error")}),
                    "created_at": str(row.get("created_at") or ""),
                }
                self._upsert_event(events, positions, event)
            try:
                ui_rows = mysql_store.fetch_all(
                    "SELECT event_id, event_type, status, summary, payload_json, created_at "
                    "FROM scholar_conversation_events WHERE tenant_id=? AND user_id=? AND conversation_id=? "
                    "ORDER BY created_at ASC",
                    (user.tenant_id, user.user_id, conversation_id),
                )
            except Exception:
                ui_rows = []
            for row in ui_rows:
                events.append({
                    "call_id": str(row.get("event_id") or ""),
                    "tool_name": str(row.get("event_type") or "ui_event"),
                    "status": str(row.get("status") or "succeeded"),
                    "arguments": {},
                    "result": mysql_store.decode_json(row.get("payload_json"), {}),
                    "summary": str(row.get("summary") or ""),
                    "created_at": str(row.get("created_at") or ""),
                })
        return events

    @staticmethod
    def _upsert_event(events: list[dict[str, Any]], positions: dict[str, int], event: dict[str, Any]) -> None:
        call_id = str(event.get("call_id") or "")
        if call_id and call_id in positions:
            events[positions[call_id]] = event
        else:
            if call_id:
                positions[call_id] = len(events)
            events.append(event)

    @staticmethod
    def record_event(
        user: UserContext, conversation_id: str, event_type: str,
        summary: str, payload: dict[str, Any], status: str = "succeeded",
    ) -> dict[str, Any]:
        event_id = f"evt_{uuid.uuid4().hex}"
        mysql_store.execute(
            "INSERT INTO scholar_conversation_events "
            "(event_id, conversation_id, tenant_id, user_id, event_type, status, summary, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, conversation_id, user.tenant_id, user.user_id, event_type, status, summary,
             json.dumps(payload, ensure_ascii=False)),
        )
        return {"event_id": event_id, "event_type": event_type, "status": status, "summary": summary, "payload": payload}

    @staticmethod
    def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
        items = result.get("items") or []
        compact_items = [
            {key: item.get(key) for key in ("paper_id", "title", "source", "year", "doi") if item.get(key) is not None}
            for item in items[:5] if isinstance(item, dict)
        ]
        paper = result.get("paper") or {}
        return {
            "status": result.get("status"),
            "items": compact_items,
            "paper": {key: paper.get(key) for key in ("paper_id", "title", "source", "doi") if paper.get(key) is not None},
            "error": result.get("error"),
        }

    def _summarize_middle(self, middle: list[dict[str, Any]], previous: str, events: list[dict[str, Any]]) -> str:
        facts: list[str] = []
        if previous:
            facts.extend(line for line in previous.splitlines()[-12:] if line.strip())
        for message in middle[-16:]:
            role = "用户" if message.get("role") == "user" else "助手"
            text = " ".join(str(message.get("content") or "").split())[:240]
            if text:
                facts.append(f"- {role}：{text}")
        if events:
            facts.append("- 已执行工具：" + "、".join(event["tool_name"] for event in events[-8:]))
        deduped = list(dict.fromkeys(facts))
        return "\n".join(deduped)[-5000:]

    @staticmethod
    def _derive_state(
        messages: list[dict[str, Any]], events: list[dict[str, Any]], previous: dict[str, Any]
    ) -> dict[str, Any]:
        latest_user = next(
            (str(item.get("content") or "")[:500] for item in reversed(messages) if item.get("role") == "user"),
            str(previous.get("current_goal") or ""),
        )
        pending = [
            {"call_id": event.get("call_id"), "tool": event.get("tool_name")}
            for event in events if event.get("status") in {"pending", "awaiting_confirmation"}
        ][-5:]
        completed = [
            {"tool": event.get("tool_name"), "result": event.get("result")}
            for event in events if event.get("status") == "succeeded"
        ][-8:]
        return {
            "current_goal": latest_user,
            "pending_confirmations": pending,
            "recent_completed_actions": completed,
        }

    @staticmethod
    def _dedupe(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[Any] = set()
        for message in messages:
            key = message.get("message_id") or id(message)
            if key not in seen:
                seen.add(key)
                result.append(message)
        return result

    @staticmethod
    def _render_messages(messages: list[dict[str, Any]]) -> str:
        return "\n".join(
            f"{'用户' if item.get('role') == 'user' else '助手'}：{str(item.get('content') or '')[:1600]}"
            for item in messages
        )

    def _render_events(self, events: list[dict[str, Any]]) -> str:
        return "\n".join(f"- {self._event_sentence(event)}" for event in events)

    @staticmethod
    def _event_sentence(event: dict[str, Any]) -> str:
        names = {
            "search_cnki_papers": "在知网检索论文",
            "download_cnki_selections": "下载选中的知网论文并写入知识库",
            "search_papers": "检索论文或知识库",
            "acquire_paper_to_knowledge": "下载论文全文并写入知识库",
            "save_to_knowledge": "保存论文到知识库",
            "inspect_reader": "检查知识库阅读效果",
        }
        if event.get("summary"):
            return f"{event.get('summary')}，状态：{event.get('status')}"
        result = event.get("result") or {}
        items = result.get("items") or []
        paper = result.get("paper") or {}
        detail = ""
        if items:
            detail = "，结果包括：" + "，".join(str(item.get("title") or item.get("paper_id")) for item in items[:3])
        elif paper:
            detail = f"，文献：{paper.get('title') or paper.get('paper_id')}"
        elif result.get("error"):
            detail = f"，错误：{result.get('error')}"
        return f"{names.get(event.get('tool_name'), event.get('tool_name'))}，状态：{event.get('status')}{detail}"

    @staticmethod
    def _load(user: UserContext, conversation_id: str) -> dict[str, Any]:
        row = mysql_store.fetch_one(
            "SELECT summary, state_json, token_estimate, compression_count FROM scholar_conversation_context "
            "WHERE tenant_id=? AND user_id=? AND conversation_id=?",
            (user.tenant_id, user.user_id, conversation_id),
        )
        return row or {}

    @staticmethod
    def _save(
        user: UserContext, conversation_id: str, summary: str, events: list[dict[str, Any]],
        state: dict[str, Any], tokens: int, count: int,
    ) -> None:
        payload = {"events": events, **state}
        mysql_store.execute(
            "INSERT OR REPLACE INTO scholar_conversation_context "
            "(conversation_id, tenant_id, user_id, summary, state_json, token_estimate, compression_count, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (conversation_id, user.tenant_id, user.user_id, summary, json.dumps(payload, ensure_ascii=False), tokens, count),
        )


conversation_context_manager = ConversationContextManager()
