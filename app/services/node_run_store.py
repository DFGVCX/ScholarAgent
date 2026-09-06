from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any
import uuid

from agents.task_graph import TaskGraphPlan
from app.services import mysql_store


NODE_RUN_SCHEMA = """CREATE TABLE IF NOT EXISTS scholar_task_node_runs (
    run_id VARCHAR(96) PRIMARY KEY, task_id VARCHAR(96) NOT NULL,
    tenant_id VARCHAR(96) NOT NULL, user_id VARCHAR(96) NOT NULL,
    node_id VARCHAR(128) NOT NULL, capability VARCHAR(96) NOT NULL,
    node_version VARCHAR(32) NOT NULL, attempt INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL, input_fingerprint VARCHAR(128) NOT NULL,
    input_json TEXT NOT NULL, output_json TEXT NOT NULL,
    dependency_snapshot_json TEXT NOT NULL, quality_json TEXT NOT NULL,
    invalidated_by VARCHAR(128), reused_from_run_id VARCHAR(96),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ
)"""


@dataclass(frozen=True)
class NodeRun:
    run_id: str
    node_id: str
    capability: str
    version: str
    attempt: int
    status: str
    input_fingerprint: str
    input: dict[str, Any]
    output: dict[str, Any]
    dependency_snapshot: dict[str, Any]
    quality: dict[str, Any]


class NodeRunStore:
    """Durable execution ledger and cache for independently recoverable nodes."""

    def __init__(self) -> None:
        self._schema_ready = False

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        mysql_store.execute(NODE_RUN_SCHEMA)
        self._schema_ready = True

    @staticmethod
    def fingerprint(payload: dict[str, Any], dependencies: dict[str, Any]) -> str:
        raw = json.dumps(
            {"input": payload, "dependencies": dependencies},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def latest_completed(
        self,
        *,
        tenant_id: str,
        user_id: str,
        task_id: str,
        node_id: str,
        fingerprint: str,
    ) -> NodeRun | None:
        self._ensure_schema()
        row = mysql_store.fetch_one(
            "SELECT * FROM scholar_task_node_runs WHERE tenant_id=? AND user_id=? "
            "AND task_id=? AND node_id=? AND input_fingerprint=? AND status='completed' "
            "ORDER BY attempt DESC LIMIT 1",
            (tenant_id, user_id, task_id, node_id, fingerprint),
        )
        return self._decode(row) if row else None

    def start(
        self,
        *,
        tenant_id: str,
        user_id: str,
        task_id: str,
        node_id: str,
        capability: str,
        version: str,
        fingerprint: str,
        payload: dict[str, Any],
        dependencies: dict[str, Any],
    ) -> str:
        self._ensure_schema()
        row = mysql_store.fetch_one(
            "SELECT MAX(attempt) AS attempt FROM scholar_task_node_runs "
            "WHERE tenant_id=? AND user_id=? AND task_id=? AND node_id=?",
            (tenant_id, user_id, task_id, node_id),
        )
        attempt = int((row or {}).get("attempt") or 0) + 1
        run_id = f"node_run_{uuid.uuid4().hex}"
        mysql_store.execute(
            "INSERT INTO scholar_task_node_runs "
            "(run_id,task_id,tenant_id,user_id,node_id,capability,node_version,attempt,status,"
            "input_fingerprint,input_json,output_json,dependency_snapshot_json,quality_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id, task_id, tenant_id, user_id, node_id, capability, version,
                attempt, "running", fingerprint, mysql_store.encode_json(payload), "{}",
                mysql_store.encode_json(dependencies), "{}",
            ),
        )
        return run_id

    def complete(self, run_id: str, output: dict[str, Any], quality: dict[str, Any] | None = None) -> None:
        self._ensure_schema()
        mysql_store.execute(
            "UPDATE scholar_task_node_runs SET status='completed', output_json=?, quality_json=?, "
            "updated_at=datetime('now'), completed_at=datetime('now') WHERE run_id=?",
            (mysql_store.encode_json(output), mysql_store.encode_json(quality or {}), run_id),
        )

    def fail(self, run_id: str, error: str) -> None:
        self._ensure_schema()
        mysql_store.execute(
            "UPDATE scholar_task_node_runs SET status='failed', quality_json=?, "
            "updated_at=datetime('now'), completed_at=datetime('now') WHERE run_id=?",
            (mysql_store.encode_json({"error": error}), run_id),
        )

    def invalidate(self, plan: TaskGraphPlan, state: dict[str, Any], retry_target: str) -> list[str]:
        self._ensure_schema()
        target = retry_target.strip()
        if target.startswith("section:"):
            node_ids = [
                target,
                plan.node_for_capability("section_writing").node_id,
                plan.node_for_capability("quality_review").node_id,
            ]
        else:
            capability = {
                "retrieval": "literature_retrieval",
                "outline": "outline_generation",
                "section_writing": "section_writing",
                "quality": "quality_review",
            }.get(target, target)
            try:
                root = plan.node_for_capability(capability).node_id
            except StopIteration:
                root = plan.node_for_capability("section_writing").node_id
            node_ids = sorted(plan.descendants(root))
        placeholders = ",".join("?" for _ in node_ids)
        mysql_store.execute(
            f"UPDATE scholar_task_node_runs SET status='invalidated', invalidated_by=?, "
            f"updated_at=datetime('now') WHERE tenant_id=? AND user_id=? AND task_id=? "
            f"AND node_id IN ({placeholders}) AND status='completed'",
            (retry_target, state["tenant_id"], state["user_id"], state["task_id"], *node_ids),
        )
        return node_ids

    def list_task_runs(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        self._ensure_schema()
        rows = mysql_store.fetch_all(
            "SELECT * FROM scholar_task_node_runs WHERE tenant_id=? AND user_id=? AND task_id=? "
            "ORDER BY created_at, attempt",
            (state["tenant_id"], state["user_id"], state["task_id"]),
        )
        return [self._decode(row).__dict__ for row in rows]

    @staticmethod
    def _decode(row: dict[str, Any]) -> NodeRun:
        return NodeRun(
            run_id=row["run_id"],
            node_id=row["node_id"],
            capability=row["capability"],
            version=row["node_version"],
            attempt=int(row.get("attempt") or 1),
            status=row["status"],
            input_fingerprint=row["input_fingerprint"],
            input=mysql_store.decode_json(row.get("input_json"), {}),
            output=mysql_store.decode_json(row.get("output_json"), {}),
            dependency_snapshot=mysql_store.decode_json(row.get("dependency_snapshot_json"), {}),
            quality=mysql_store.decode_json(row.get("quality_json"), {}),
        )


node_run_store = NodeRunStore()
