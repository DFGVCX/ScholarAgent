from __future__ import annotations

import json
from typing import Any
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def retrieval_attribution(returned: set[str], adopted: set[str]) -> str:
    if not returned:
        return "rag_not_retrieved"
    if not adopted.intersection(returned):
        return "agent_not_adopted"
    return "evidence_adopted"


class RetrievalReplayStore:
    async def record(
        self,
        session: AsyncSession,
        tenant_id: str,
        user_id: str,
        response: dict[str, Any],
        *,
        consumer: str,
        conversation_id: str = "",
        task_id: str = "",
    ) -> str:
        replay_id = f"retrieval_{uuid.uuid4().hex}"
        hits = list(response.get("local_hits") or response.get("items") or [])
        returned = {str(hit.get("chunk_id") or "") for hit in hits if hit.get("chunk_id")}
        debug = dict(response.get("debug") or {})
        await session.execute(
            text(
                "INSERT INTO rag_retrieval_replays "
                "(replay_id,tenant_id,user_id,conversation_id,task_id,consumer,query,"
                "requested_mode,effective_mode,candidate_snapshot,context_snapshot,"
                "adopted_chunk_ids,attribution,warnings,timings) VALUES "
                "(:replay_id,:tenant_id,:user_id,:conversation_id,:task_id,:consumer,:query,"
                ":requested_mode,:effective_mode,CAST(:candidates AS JSONB),"
                "CAST(:contexts AS JSONB),CAST(:adopted AS JSONB),:attribution,"
                "CAST(:warnings AS JSONB),CAST(:timings AS JSONB))"
            ),
            {
                "replay_id": replay_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "conversation_id": conversation_id or None,
                "task_id": task_id or None,
                "consumer": consumer or "api",
                "query": str(response.get("query") or ""),
                "requested_mode": str((response.get("ranking_policy") or {}).get("requested_mode") or response.get("retrieval_mode") or ""),
                "effective_mode": str(response.get("retrieval_mode") or ""),
                "candidates": json.dumps(
                    {
                        "candidate_pools": debug.get("candidate_pools") or {},
                        "reproducibility": response.get("reproducibility") or {},
                    },
                    ensure_ascii=False,
                ),
                "contexts": json.dumps(hits, ensure_ascii=False, default=str),
                "adopted": "[]",
                "attribution": retrieval_attribution(returned, set()),
                "warnings": json.dumps(response.get("warnings") or [], ensure_ascii=False),
                "timings": json.dumps(debug.get("timings_ms") or {}, ensure_ascii=False),
            },
        )
        return replay_id

    async def mark_adoption(
        self,
        session: AsyncSession,
        tenant_id: str,
        user_id: str,
        replay_id: str,
        chunk_ids: list[str],
    ) -> dict[str, Any]:
        result = await session.execute(
            text(
                "SELECT context_snapshot FROM rag_retrieval_replays "
                "WHERE tenant_id=:tenant_id AND user_id=:user_id AND replay_id=:replay_id"
            ),
            {"tenant_id": tenant_id, "user_id": user_id, "replay_id": replay_id},
        )
        row = result.mappings().first()
        if not row:
            raise LookupError("retrieval replay not found")
        contexts = row["context_snapshot"] or []
        returned = {
            str(item.get("chunk_id") or "")
            for item in contexts
            if isinstance(item, dict) and item.get("chunk_id")
        }
        adopted = {str(value) for value in chunk_ids if str(value) in returned}
        attribution = retrieval_attribution(returned, adopted)
        await session.execute(
            text(
                "UPDATE rag_retrieval_replays SET adopted_chunk_ids=CAST(:adopted AS JSONB), "
                "attribution=:attribution, updated_at=CURRENT_TIMESTAMP "
                "WHERE tenant_id=:tenant_id AND user_id=:user_id AND replay_id=:replay_id"
            ),
            {
                "adopted": json.dumps(sorted(adopted), ensure_ascii=False),
                "attribution": attribution,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "replay_id": replay_id,
            },
        )
        return {
            "replay_id": replay_id,
            "returned_chunk_count": len(returned),
            "adopted_chunk_ids": sorted(adopted),
            "attribution": attribution,
        }

    async def recent(
        self, session: AsyncSession, tenant_id: str, user_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text(
                "SELECT replay_id,conversation_id,task_id,consumer,query,requested_mode,"
                "effective_mode,adopted_chunk_ids,attribution,warnings,timings,"
                "candidate_snapshot->'reproducibility' AS reproducibility,created_at "
                "FROM rag_retrieval_replays WHERE tenant_id=:tenant_id AND user_id=:user_id "
                "ORDER BY created_at DESC LIMIT :limit"
            ),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "limit": max(1, min(int(limit), 100)),
            },
        )
        return [dict(row) for row in result.mappings().all()]


retrieval_replay_store = RetrievalReplayStore()
