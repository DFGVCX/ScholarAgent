from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.schemas import UserContext
from app.services import mysql_store
from app.config import get_settings


_SENSITIVE = re.compile(r"token|password|secret|api.?key|authorization", re.IGNORECASE)
_TOOL_LABELS = {
    "search_papers": "论文检索",
    "search_cnki_papers": "知网论文检索",
    "download_cnki_selections": "知网论文下载入库",
    "acquire_paper_to_knowledge": "论文全文入库",
    "inspect_reader": "论文阅读检查",
    "translate_reader_text": "论文段落翻译",
}


class SkillEvolutionService:
    """Mine successful operation patterns into review-only Skill candidates."""

    def __init__(self, *, minimum_evidence: int = 3, minimum_success_rate: float = 0.8) -> None:
        self.minimum_evidence = minimum_evidence
        self.minimum_success_rate = minimum_success_rate

    def record_tool_outcome(
        self,
        user: UserContext,
        conversation_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        status: str,
    ) -> dict[str, Any] | None:
        recipe = {
            "kind": "tool",
            "steps": [{"tool_name": tool_name, "arguments": self._argument_recipe(arguments)}],
            "source": {"conversation_id": conversation_id},
        }
        return self._record(user, tool_name, recipe, status)

    def record_pipeline_outcome(
        self,
        user: UserContext,
        conversation_id: str,
        steps: list[dict[str, Any]],
        status: str,
    ) -> dict[str, Any] | None:
        normalized_steps = [
            {
                "tool_name": str(step.get("tool_name") or ""),
                "arguments": self._argument_recipe(step.get("arguments") or {}),
            }
            for step in steps
            if step.get("tool_name")
        ]
        if len(normalized_steps) < 2:
            return None
        operation_name = "_then_".join(step["tool_name"] for step in normalized_steps)
        recipe = {
            "kind": "pipeline",
            "steps": normalized_steps,
            "source": {"conversation_id": conversation_id},
        }
        return self._record(user, operation_name, recipe, status)

    def list_candidates(self, user: UserContext, status: str = "draft") -> list[dict[str, Any]]:
        rows = mysql_store.fetch_all(
            "SELECT candidate_id,pattern_id,name,description,manifest_json,evidence_count,"
            "success_rate,status,created_at,updated_at FROM scholar_skill_candidates "
            "WHERE tenant_id=? AND user_id=? AND status=? ORDER BY updated_at DESC",
            (user.tenant_id, user.user_id, status),
        )
        for row in rows:
            row["manifest"] = mysql_store.decode_json(row.pop("manifest_json", None), {})
        return rows

    def review_candidate(
        self, user: UserContext, candidate_id: str, *, approved: bool
    ) -> dict[str, Any] | None:
        row = mysql_store.fetch_one(
            "SELECT candidate_id,name,description,manifest_json,evidence_count,success_rate,status "
            "FROM scholar_skill_candidates WHERE tenant_id=? AND user_id=? AND candidate_id=?",
            (user.tenant_id, user.user_id, candidate_id),
        )
        if not row:
            return None
        status = "approved" if approved else "rejected"
        artifact_path = ""
        manifest = mysql_store.decode_json(row.get("manifest_json"), {})
        if approved:
            artifact_path = str(self._export_candidate(user, candidate_id, manifest))
        mysql_store.execute(
            "UPDATE scholar_skill_candidates SET status=?,updated_at=datetime('now') "
            "WHERE tenant_id=? AND user_id=? AND candidate_id=?",
            (status, user.tenant_id, user.user_id, candidate_id),
        )
        return {
            "candidate_id": candidate_id,
            "status": status,
            "artifact_path": artifact_path,
            "manifest": manifest,
            "live": False,
        }

    def _record(
        self,
        user: UserContext,
        operation_name: str,
        recipe: dict[str, Any],
        status: str,
    ) -> dict[str, Any] | None:
        signature_payload = {
            "operation_name": operation_name,
            "steps": recipe["steps"],
        }
        signature = json.dumps(signature_payload, ensure_ascii=False, sort_keys=True)
        pattern_id = "pat_" + hashlib.sha256(
            f"{user.tenant_id}:{user.user_id}:{signature}".encode("utf-8")
        ).hexdigest()[:24]
        row = mysql_store.fetch_one(
            "SELECT success_count,failure_count FROM scholar_operation_patterns "
            "WHERE tenant_id=? AND user_id=? AND signature=?",
            (user.tenant_id, user.user_id, signature),
        )
        succeeded = status == "succeeded"
        if row:
            mysql_store.execute(
                "UPDATE scholar_operation_patterns SET success_count=success_count+?,"
                "failure_count=failure_count+?,last_seen_at=datetime('now'),recipe_json=? "
                "WHERE tenant_id=? AND user_id=? AND signature=?",
                (
                    1 if succeeded else 0, 0 if succeeded else 1,
                    mysql_store.encode_json(recipe), user.tenant_id, user.user_id, signature,
                ),
            )
            successes = int(row.get("success_count") or 0) + (1 if succeeded else 0)
            failures = int(row.get("failure_count") or 0) + (0 if succeeded else 1)
        else:
            mysql_store.execute(
                "INSERT INTO scholar_operation_patterns "
                "(pattern_id,tenant_id,user_id,operation_name,signature,recipe_json,success_count,failure_count) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    pattern_id, user.tenant_id, user.user_id, operation_name, signature,
                    mysql_store.encode_json(recipe), 1 if succeeded else 0, 0 if succeeded else 1,
                ),
            )
            successes, failures = (1, 0) if succeeded else (0, 1)
        total = successes + failures
        success_rate = successes / max(1, total)
        if successes < self.minimum_evidence or success_rate < self.minimum_success_rate:
            return None
        return self._upsert_candidate(
            user, pattern_id, operation_name, recipe, successes, success_rate
        )

    def _upsert_candidate(
        self,
        user: UserContext,
        pattern_id: str,
        operation_name: str,
        recipe: dict[str, Any],
        evidence_count: int,
        success_rate: float,
    ) -> dict[str, Any]:
        candidate_id = "skillcand_" + pattern_id.removeprefix("pat_")
        label = _TOOL_LABELS.get(operation_name, operation_name.replace("_then_", " + "))
        name = "learned_" + re.sub(r"[^a-z0-9_]+", "_", operation_name.lower())[:48].strip("_")
        description = f"根据 {evidence_count} 次成功操作沉淀的“{label}”候选流程，发布前必须人工审核。"
        manifest = {
            "name": name,
            "version": "0.1.0-draft",
            "enabled": False,
            "description": description,
            "recipe": recipe,
            "governance": {
                "auto_publish": False,
                "requires_review": True,
                "evidence_count": evidence_count,
                "success_rate": round(success_rate, 4),
            },
        }
        existing = mysql_store.fetch_one(
            "SELECT candidate_id FROM scholar_skill_candidates WHERE tenant_id=? AND user_id=? AND pattern_id=?",
            (user.tenant_id, user.user_id, pattern_id),
        )
        if existing:
            mysql_store.execute(
                "UPDATE scholar_skill_candidates SET description=?,manifest_json=?,evidence_count=?,"
                "success_rate=?,updated_at=datetime('now') WHERE tenant_id=? AND user_id=? AND pattern_id=?",
                (
                    description, mysql_store.encode_json(manifest), evidence_count, success_rate,
                    user.tenant_id, user.user_id, pattern_id,
                ),
            )
        else:
            mysql_store.execute(
                "INSERT INTO scholar_skill_candidates "
                "(candidate_id,pattern_id,tenant_id,user_id,name,description,manifest_json,evidence_count,success_rate,status) "
                "VALUES (?,?,?,?,?,?,?,?,?,'draft')",
                (
                    candidate_id, pattern_id, user.tenant_id, user.user_id, name, description,
                    mysql_store.encode_json(manifest), evidence_count, success_rate,
                ),
            )
        return manifest | {"candidate_id": candidate_id, "status": "draft"}

    @classmethod
    def _argument_recipe(cls, arguments: dict[str, Any]) -> dict[str, Any]:
        recipe: dict[str, Any] = {}
        variable_fields = {"query", "paper_id", "source_url", "conversation_id", "title", "content"}
        ignored = {"tenant_id", "user_id", "confirmation_token", "task_id"}
        for key, value in sorted(arguments.items()):
            if key in ignored or _SENSITIVE.search(key):
                continue
            if key in variable_fields:
                recipe[key] = "{{" + key + "}}"
            elif isinstance(value, (str, int, float, bool)):
                recipe[key] = value
            elif isinstance(value, list):
                recipe[key] = "{{" + key + "}}"
        return recipe

    @staticmethod
    def _export_candidate(
        user: UserContext, candidate_id: str, manifest: dict[str, Any]
    ) -> Path:
        safe_tenant = re.sub(r"[^a-zA-Z0-9_-]+", "_", user.tenant_id)[:80]
        safe_candidate = re.sub(r"[^a-zA-Z0-9_-]+", "_", candidate_id)[:80]
        target = get_settings().storage_dir / "skill_candidates" / safe_tenant / safe_candidate
        target.mkdir(parents=True, exist_ok=True)
        path = target / "SKILL.md"
        recipe = json.dumps(manifest.get("recipe") or {}, ensure_ascii=False, indent=2)
        path.write_text(
            "---\n"
            f"name: {manifest.get('name', safe_candidate)}\n"
            f"version: {manifest.get('version', '0.1.0-draft')}\n"
            "enabled: false\n"
            f"description: {json.dumps(str(manifest.get('description') or ''), ensure_ascii=False)}\n"
            "---\n\n"
            "# 审核后的 Skill 候选\n\n"
            "该文件由成功操作轨迹确定性生成，当前不会被生产 SkillRegistry 自动加载。\n"
            "补充业务输入输出契约、测试和正式 workflow 入口后，才能发布到 `skills/`。\n\n"
            "## 操作配方\n\n```json\n" + recipe + "\n```\n",
            encoding="utf-8",
        )
        return path


skill_evolution_service = SkillEvolutionService()
