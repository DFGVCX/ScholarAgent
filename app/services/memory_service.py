from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.schemas import UserContext
from app.services import mysql_store


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    memory_type: str
    content: str
    importance: float
    confidence: float
    score: float = 0.0


class UserMemoryService:
    """Tenant-scoped durable memory with deterministic extraction and hybrid recall."""

    _explicit_patterns = (
        ("preference", re.compile(r"(?:我|本人)?(?:偏好|喜欢|习惯|倾向于)(.+)"), 0.86),
        ("constraint", re.compile(r"(?:以后|后续|始终|一直)(?:请|要|需要)?(.+)"), 0.82),
        ("constraint", re.compile(r"(?:不要|不能|禁止)(.+)"), 0.88),
        ("profile", re.compile(r"(?:我的|我目前的)(?:研究方向|课题|项目|领域)(?:是|为|：|:)?(.+)"), 0.9),
        ("preference", re.compile(r"(?:引用|参考文献)(?:格式|风格)(?:使用|采用|是|为|：|:)?(.+)"), 0.88),
    )
    _remember_pattern = re.compile(r"(?:请)?记住(?:我|：|:)?(.+)")
    _stop_words = frozenset("的 了 和 是 我 你 在 要 有 就 都 也 及 与 或 把 给 请 这 那 一个 当前 进行 可以".split())

    def __init__(self) -> None:
        self._schema_ready = False

    def ensure_schema(self) -> None:
        self._schema_ready = True

    @staticmethod
    def _normalize(content: str) -> str:
        return re.sub(r"\s+", " ", content.strip().lower()).strip("。；;，, ")

    def remember(
        self,
        user: UserContext,
        *,
        memory_type: str,
        content: str,
        conversation_id: str = "",
        source_message_id: str = "",
        importance: float = 0.7,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord | None:
        self.ensure_schema()
        normalized = self._normalize(content)
        if len(normalized) < 3:
            return None
        digest = hashlib.sha256(
            f"{user.tenant_id}|{user.user_id}|{memory_type}|{normalized}".encode("utf-8")
        ).hexdigest()[:32]
        memory_id = f"mem_{digest}"
        mysql_store.execute(
            "INSERT INTO scholar_memories "
            "(memory_id, tenant_id, user_id, conversation_id, memory_type, content, "
            "normalized_content, importance, confidence, source_message_id, metadata_json, "
            "access_count, last_accessed_at, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "COALESCE((SELECT access_count FROM scholar_memories WHERE memory_id=?), 0), "
            "(SELECT last_accessed_at FROM scholar_memories WHERE memory_id=?), 'active', "
            "COALESCE((SELECT created_at FROM scholar_memories WHERE memory_id=?), datetime('now')), datetime('now')) "
            "ON CONFLICT (memory_id) DO UPDATE SET conversation_id=EXCLUDED.conversation_id, "
            "content=EXCLUDED.content, importance=EXCLUDED.importance, confidence=EXCLUDED.confidence, "
            "source_message_id=EXCLUDED.source_message_id, metadata_json=EXCLUDED.metadata_json, "
            "status='active', updated_at=CURRENT_TIMESTAMP",
            (
                memory_id, user.tenant_id, user.user_id, conversation_id or None,
                memory_type, content.strip(), normalized, float(importance), float(confidence),
                source_message_id or None, mysql_store.encode_json(metadata or {}),
                memory_id, memory_id, memory_id,
            ),
        )
        return MemoryRecord(memory_id, memory_type, content.strip(), importance, confidence)

    def extract_from_messages(
        self, user: UserContext, conversation_id: str, messages: list[dict[str, Any]]
    ) -> list[MemoryRecord]:
        saved: list[MemoryRecord] = []
        for message in messages[-6:]:
            if message.get("role") != "user":
                continue
            text = " ".join(str(message.get("content") or "").split())[:1200]
            if not text:
                continue
            candidates: list[tuple[str, str, float]] = []
            explicit = self._remember_pattern.search(text)
            if explicit:
                candidates.append(("instruction", explicit.group(1), 0.95))
            for memory_type, pattern, importance in self._explicit_patterns:
                match = pattern.search(text)
                if match:
                    candidates.append((memory_type, match.group(0), importance))
            for memory_type, content, importance in candidates[:3]:
                record = self.remember(
                    user,
                    memory_type=memory_type,
                    content=content[:500],
                    conversation_id=conversation_id,
                    source_message_id=str(message.get("message_id") or ""),
                    importance=importance,
                    metadata={"extractor": "deterministic-v1"},
                )
                if record:
                    saved.append(record)
        return saved

    def recall(self, user: UserContext, query: str, limit: int = 8) -> list[MemoryRecord]:
        self.ensure_schema()
        rows = mysql_store.fetch_all(
            "SELECT memory_id, memory_type, content, normalized_content, importance, confidence, "
            "access_count, updated_at FROM scholar_memories "
            "WHERE tenant_id=? AND user_id=? AND status='active' "
            "ORDER BY importance DESC, updated_at DESC LIMIT 100",
            (user.tenant_id, user.user_id),
        )
        query_tokens = self._tokens(query)
        now = datetime.now(timezone.utc)
        scored: list[MemoryRecord] = []
        for row in rows:
            memory_tokens = self._tokens(str(row.get("normalized_content") or row.get("content") or ""))
            overlap = len(query_tokens & memory_tokens) / max(1, len(query_tokens | memory_tokens))
            updated = self._parse_datetime(row.get("updated_at"))
            age_days = max(0.0, (now - updated).total_seconds() / 86400)
            recency = math.exp(-age_days / 90)
            importance = float(row.get("importance") or 0.5)
            confidence = float(row.get("confidence") or 1.0)
            score = overlap * 0.55 + importance * 0.25 + recency * 0.15 + confidence * 0.05
            if overlap > 0 or importance >= 0.82:
                scored.append(MemoryRecord(
                    str(row["memory_id"]), str(row["memory_type"]), str(row["content"]),
                    importance, confidence, score,
                ))
        selected = sorted(scored, key=lambda item: item.score, reverse=True)[:limit]
        if selected:
            placeholders = ",".join("?" for _ in selected)
            mysql_store.execute(
                f"UPDATE scholar_memories SET access_count=access_count+1, "
                f"last_accessed_at=datetime('now') WHERE tenant_id=? AND user_id=? "
                f"AND memory_id IN ({placeholders})",
                (user.tenant_id, user.user_id, *(item.memory_id for item in selected)),
            )
        return selected

    def forget(self, user: UserContext, memory_id: str) -> bool:
        self.ensure_schema()
        return mysql_store.execute(
            "UPDATE scholar_memories SET status='forgotten', updated_at=datetime('now') "
            "WHERE tenant_id=? AND user_id=? AND memory_id=?",
            (user.tenant_id, user.user_id, memory_id),
        ) > 0

    def list_memories(self, user: UserContext, limit: int = 50) -> list[dict[str, Any]]:
        self.ensure_schema()
        return mysql_store.fetch_all(
            "SELECT memory_id, memory_type, content, importance, confidence, access_count, "
            "conversation_id, created_at, updated_at FROM scholar_memories "
            "WHERE tenant_id=? AND user_id=? AND status='active' "
            "ORDER BY importance DESC, updated_at DESC LIMIT ?",
            (user.tenant_id, user.user_id, max(1, min(limit, 200))),
        )

    @classmethod
    def _tokens(cls, text: str) -> set[str]:
        latin = re.findall(r"[a-z0-9][a-z0-9._/-]+", text.lower())
        chinese = re.findall(r"[\u4e00-\u9fff]{2,}", text)
        grams: list[str] = []
        for block in chinese:
            grams.extend(block[index:index + 2] for index in range(max(1, len(block) - 1)))
        return {token for token in (*latin, *grams) if token not in cls._stop_words}

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if not value:
            return datetime.now(timezone.utc)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return datetime.now(timezone.utc)


user_memory_service = UserMemoryService()
