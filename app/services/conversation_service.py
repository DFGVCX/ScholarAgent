from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.factory import model_factory
from app.config import get_settings
from app.schemas import UserContext
from app.services import mysql_store
from app.services.rag_service import rag_service


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


SKILL_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "skill_id": "general_assistant",
        "name": "通用研究对话",
        "category": "workspace",
        "status": "available",
        "description": "承接需求拆解、资料整理、任务规划和后续 Skill 分发。",
        "entrypoint": "conversation",
        "placeholder": "描述你要完成的工作，系统会保存为一个可继续推进的会话。",
    },
    {
        "skill_id": "survey_review",
        "name": "学术综述生成",
        "category": "generation",
        "status": "implemented",
        "description": "接入现有任务生成流程，支持大纲确认、正文生成和引用审计。",
        "entrypoint": "tasks.survey",
        "placeholder": "例如：生成一篇关于多模态医疗影像模型可靠性的综述。",
    },
    {
        "skill_id": "knowledge_base",
        "name": "个人知识库检索",
        "category": "retrieval",
        "status": "implemented",
        "description": "查询租户内论文、RAG 片段和原文资料。",
        "entrypoint": "knowledge.rag",
        "placeholder": "输入要检索的论文主题、方法名或问题。",
    },
    {
        "skill_id": "citation_audit",
        "name": "引用审计",
        "category": "audit",
        "status": "implemented",
        "description": "查看已完成任务的引用覆盖、缺失引用和疑似幻觉引用。",
        "entrypoint": "tasks.audit",
        "placeholder": "输入任务 ID 或说明要检查的引用问题。",
    },
    {
        "skill_id": "document_report",
        "name": "文档报告生成",
        "category": "document",
        "status": "planned",
        "description": "预留给 DOCX、课程报告、企业报告等外部 Skill 接入。",
        "entrypoint": "external.skill",
        "placeholder": "描述报告类型、格式和材料，后续可绑定文档生成 Skill。",
    },
    {
        "skill_id": "custom_skill",
        "name": "自定义 Skill",
        "category": "extension",
        "status": "planned",
        "description": "面向后续公司级 Skill 插件或 Agent 编排扩展。",
        "entrypoint": "external.registry",
        "placeholder": "输入自定义 Skill 名称、输入材料和期望输出。",
    },
)


CONVERSATION_SCHEMA_SQL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS scholar_conversations (
        conversation_id CHAR(36) PRIMARY KEY,
        tenant_id VARCHAR(64) NOT NULL,
        user_id VARCHAR(64) NOT NULL,
        title VARCHAR(240) NOT NULL,
        skill_id VARCHAR(120) NOT NULL DEFAULT 'general_assistant',
        status VARCHAR(32) NOT NULL DEFAULT 'active',
        metadata_json JSON NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        KEY idx_scholar_conversations_user (tenant_id, user_id, updated_at),
        KEY idx_scholar_conversations_skill (tenant_id, user_id, skill_id),
        CONSTRAINT fk_scholar_conversations_user_runtime
            FOREIGN KEY (user_id) REFERENCES scholar_users(user_id)
            ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS scholar_conversation_messages (
        message_id CHAR(36) PRIMARY KEY,
        conversation_id CHAR(36) NOT NULL,
        tenant_id VARCHAR(64) NOT NULL,
        user_id VARCHAR(64) NOT NULL,
        role VARCHAR(32) NOT NULL,
        content MEDIUMTEXT NOT NULL,
        skill_id VARCHAR(120) NULL,
        metadata_json JSON NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        KEY idx_scholar_conversation_messages_conversation (tenant_id, conversation_id, created_at),
        CONSTRAINT fk_scholar_conversation_messages_conversation_runtime
            FOREIGN KEY (conversation_id) REFERENCES scholar_conversations(conversation_id)
            ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
)


class ConversationRepository:
    def __init__(self, path: Path | None = None) -> None:
        settings = get_settings()
        self.path = path or settings.storage_dir / "conversations.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._mysql_schema_ready = False

    def skills(self) -> list[dict[str, Any]]:
        return [dict(item) for item in SKILL_CATALOG]

    def _read_all_sync(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"conversations": {}, "messages": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write_all_sync(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _mysql_ready(self) -> bool:
        if not mysql_store.is_available():
            return False
        if self._mysql_schema_ready:
            return True
        for statement in CONVERSATION_SCHEMA_SQL:
            mysql_store.execute(statement)
        self._mysql_schema_ready = True
        return True

    async def create(
        self,
        user: UserContext,
        title: str,
        skill_id: str = "general_assistant",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _now()
        conversation = {
            "conversation_id": str(uuid.uuid4()),
            "tenant_id": user.tenant_id,
            "user_id": user.user_id,
            "title": title.strip()[:240] or "新的会话",
            "skill_id": skill_id or "general_assistant",
            "status": "active",
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
        }
        if self._mysql_ready():
            mysql_store.execute(
                """
                INSERT INTO scholar_conversations
                    (conversation_id, tenant_id, user_id, title, skill_id, status, metadata_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    conversation["conversation_id"],
                    user.tenant_id,
                    user.user_id,
                    conversation["title"],
                    conversation["skill_id"],
                    conversation["status"],
                    mysql_store.encode_json(conversation["metadata"]),
                ),
            )
            return conversation
        async with self._lock:
            data = self._read_all_sync()
            data["conversations"][conversation["conversation_id"]] = conversation
            data["messages"].setdefault(conversation["conversation_id"], [])
            self._write_all_sync(data)
        return conversation

    async def list_by_user(self, user: UserContext) -> list[dict[str, Any]]:
        if self._mysql_ready():
            rows = mysql_store.fetch_all(
                """
                SELECT c.*,
                       (
                           SELECT m.content
                           FROM scholar_conversation_messages m
                           WHERE m.tenant_id = c.tenant_id
                             AND m.conversation_id = c.conversation_id
                           ORDER BY m.created_at DESC
                           LIMIT 1
                       ) AS last_message
                FROM scholar_conversations c
                WHERE c.tenant_id = %s AND c.user_id = %s AND c.status <> 'archived'
                ORDER BY c.updated_at DESC
                LIMIT 80
                """,
                (user.tenant_id, user.user_id),
            )
            return [self._conversation_from_row(row) for row in rows]
        async with self._lock:
            data = self._read_all_sync()
        conversations = [
            item
            for item in data["conversations"].values()
            if item.get("tenant_id") == user.tenant_id
            and item.get("user_id") == user.user_id
            and item.get("status") != "archived"
        ]
        messages = data.get("messages", {})
        for item in conversations:
            last = (messages.get(item["conversation_id"]) or [])[-1:]
            item["last_message"] = last[0]["content"] if last else ""
        return sorted(conversations, key=lambda item: item.get("updated_at", ""), reverse=True)

    async def get(self, user: UserContext, conversation_id: str) -> dict[str, Any] | None:
        if self._mysql_ready():
            row = mysql_store.fetch_one(
                """
                SELECT *
                FROM scholar_conversations
                WHERE tenant_id = %s AND user_id = %s AND conversation_id = %s
                LIMIT 1
                """,
                (user.tenant_id, user.user_id, conversation_id),
            )
            if row is None:
                return None
            conversation = self._conversation_from_row(row)
            conversation["messages"] = await self.messages(user, conversation_id)
            return conversation
        async with self._lock:
            data = self._read_all_sync()
        conversation = data["conversations"].get(conversation_id)
        if not conversation or conversation.get("tenant_id") != user.tenant_id or conversation.get("user_id") != user.user_id:
            return None
        return {**conversation, "messages": data.get("messages", {}).get(conversation_id, [])}

    async def messages(self, user: UserContext, conversation_id: str) -> list[dict[str, Any]]:
        if self._mysql_ready():
            rows = mysql_store.fetch_all(
                """
                SELECT *
                FROM scholar_conversation_messages
                WHERE tenant_id = %s AND conversation_id = %s
                ORDER BY created_at ASC
                """,
                (user.tenant_id, conversation_id),
            )
            return [self._message_from_row(row) for row in rows]
        async with self._lock:
            data = self._read_all_sync()
        return [
            item
            for item in data.get("messages", {}).get(conversation_id, [])
            if item.get("tenant_id") == user.tenant_id
        ]

    async def add_message(
        self,
        user: UserContext,
        conversation_id: str,
        role: str,
        content: str,
        skill_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _now()
        message = {
            "message_id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "tenant_id": user.tenant_id,
            "user_id": user.user_id,
            "role": role,
            "content": content,
            "skill_id": skill_id,
            "metadata": metadata or {},
            "created_at": now,
        }
        if self._mysql_ready():
            mysql_store.execute(
                """
                INSERT INTO scholar_conversation_messages
                    (message_id, conversation_id, tenant_id, user_id, role, content, skill_id, metadata_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    message["message_id"],
                    conversation_id,
                    user.tenant_id,
                    user.user_id,
                    role,
                    content,
                    skill_id,
                    mysql_store.encode_json(message["metadata"]),
                ),
            )
            mysql_store.execute(
                """
                UPDATE scholar_conversations
                SET updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = %s AND user_id = %s AND conversation_id = %s
                """,
                (user.tenant_id, user.user_id, conversation_id),
            )
            return message
        async with self._lock:
            data = self._read_all_sync()
            if conversation_id not in data["conversations"]:
                raise KeyError(f"conversation not found: {conversation_id}")
            data["messages"].setdefault(conversation_id, []).append(message)
            data["conversations"][conversation_id]["updated_at"] = now
            self._write_all_sync(data)
        return message

    async def archive(self, user: UserContext, conversation_id: str) -> bool:
        if self._mysql_ready():
            affected = mysql_store.execute(
                """
                UPDATE scholar_conversations
                SET status = 'archived'
                WHERE tenant_id = %s AND user_id = %s AND conversation_id = %s
                """,
                (user.tenant_id, user.user_id, conversation_id),
            )
            return affected > 0
        async with self._lock:
            data = self._read_all_sync()
            conversation = data["conversations"].get(conversation_id)
            if not conversation or conversation.get("tenant_id") != user.tenant_id or conversation.get("user_id") != user.user_id:
                return False
            conversation["status"] = "archived"
            conversation["updated_at"] = _now()
            self._write_all_sync(data)
        return True

    async def dispatch_message(
        self,
        user: UserContext,
        content: str,
        skill_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        metadata = metadata or {}
        skill = next((item for item in SKILL_CATALOG if item["skill_id"] == skill_id), SKILL_CATALOG[0])
        if skill_id == "knowledge_base":
            results = await rag_service.search(user.tenant_id, user.user_id, content, 5)
            count = len(results.get("items") or [])
            return (
                f"已在个人知识库中检索到 {count} 条相关片段。右侧知识库工作台可以继续查看原文、解析正文和批注。",
                {"kind": "rag_search", "results": results},
            )
        if skill_id == "survey_review":
            return (
                "已把写作需求保存为会话。正式生成时进入“写作专项”，只填写研究主题即可自动联网检索论文池；DOI、arXiv ID 或 PDF 只是可选限定材料。",
                {"kind": "survey_intake", "entrypoint": "tasks.survey"},
            )
        if skill_id == "citation_audit":
            return (
                "已记录引用审计需求。引用审计依赖已完成的任务结果；你可以在“引用审计”工作区选择任务查看覆盖率、缺失引用和疑似幻觉引用。",
                {"kind": "citation_audit_intake", "entrypoint": "tasks.audit"},
            )
        if skill["status"] == "planned":
            return (
                f"已保存到会话。{skill['name']} 当前是预留 Skill，后续接入执行器后可以复用这条会话继续执行。",
                {"kind": "planned_skill", "entrypoint": skill["entrypoint"]},
            )
        try:
            response = await model_factory.generate_text(
                "conversation",
                content,
                {
                    "tenant_id": user.tenant_id,
                    "user_id": user.user_id,
                    "skill_id": skill_id,
                    "conversation_metadata": metadata,
                },
            )
            return (
                response.content,
                {"kind": "llm_chat", "provider": response.provider, "model": response.model},
            )
        except Exception as exc:
            return (
                f"模型调用失败：{exc}",
                {"kind": "llm_chat_error", "error": str(exc)},
            )

    def _conversation_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "conversation_id": row["conversation_id"],
            "tenant_id": row["tenant_id"],
            "user_id": row["user_id"],
            "title": row["title"],
            "skill_id": row["skill_id"],
            "status": row["status"],
            "metadata": mysql_store.decode_json(row.get("metadata_json"), {}),
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
            "last_message": row.get("last_message") or "",
        }

    def _message_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "message_id": row["message_id"],
            "conversation_id": row["conversation_id"],
            "tenant_id": row["tenant_id"],
            "user_id": row["user_id"],
            "role": row["role"],
            "content": row["content"],
            "skill_id": row.get("skill_id"),
            "metadata": mysql_store.decode_json(row.get("metadata_json"), {}),
            "created_at": str(row.get("created_at") or ""),
        }


conversation_repository = ConversationRepository()
