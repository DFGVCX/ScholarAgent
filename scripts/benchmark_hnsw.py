from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.evaluation.performance import summarize_query_plans


INDEX_NAME = "idx_paper_chunks_embedding"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark the production pgvector HNSW query with EXPLAIN ANALYZE."
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--ef-search", default="40,80,120,200")
    parser.add_argument("--minimum-vectors", type=int, default=1000)
    parser.add_argument("--allow-small-corpus", action="store_true")
    return parser.parse_args()


def _explain_payload(value: Any) -> dict[str, Any]:
    payload = json.loads(value) if isinstance(value, str) else value
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise ValueError("PostgreSQL returned an unexpected EXPLAIN JSON payload")
    return dict(payload[0])


async def _run(args: argparse.Namespace) -> None:
    from sqlalchemy import text

    from app.config import get_settings
    from app.db.session import tenant_transaction

    settings = get_settings()
    sample_count = max(1, min(int(args.samples), 500))
    result_limit = max(1, min(int(args.limit), 50))
    ef_values = sorted(
        {max(1, min(int(value.strip()), 1000)) for value in args.ef_search.split(",") if value.strip()}
    )
    if not ef_values:
        raise ValueError("at least one hnsw.ef_search value is required")

    async with tenant_transaction(args.tenant_id, args.user_id) as session:
        count_result = await session.execute(
            text(
                """SELECT COUNT(*) FROM paper_chunks c
                JOIN papers p ON p.paper_uuid=c.paper_uuid
                    AND p.tenant_id=c.tenant_id AND p.user_id=c.user_id
                WHERE c.tenant_id=:tenant_id AND c.user_id=:user_id
                    AND p.deleted_at IS NULL AND p.in_knowledge_base=true
                    AND c.content_version=p.current_content_version
                    AND c.embedding_status='ready' AND c.embedding IS NOT NULL
                    AND c.embedding_model=:embedding_model"""
            ),
            {
                "tenant_id": args.tenant_id,
                "user_id": args.user_id,
                "embedding_model": settings.rag_embedding_model,
            },
        )
        vector_count = int(count_result.scalar_one() or 0)
        if vector_count < max(1, int(args.minimum_vectors)) and not args.allow_small_corpus:
            raise RuntimeError(
                f"only {vector_count} current vectors are available; "
                f"at least {args.minimum_vectors} are required for a meaningful benchmark "
                "(use --allow-small-corpus only for a smoke run)"
            )
        sample_result = await session.execute(
            text(
                """SELECT c.embedding::text AS embedding
                FROM paper_chunks c
                JOIN papers p ON p.paper_uuid=c.paper_uuid
                    AND p.tenant_id=c.tenant_id AND p.user_id=c.user_id
                WHERE c.tenant_id=:tenant_id AND c.user_id=:user_id
                    AND p.deleted_at IS NULL AND p.in_knowledge_base=true
                    AND c.content_version=p.current_content_version
                    AND c.embedding_status='ready' AND c.embedding IS NOT NULL
                    AND c.embedding_model=:embedding_model
                ORDER BY c.chunk_uuid LIMIT :samples"""
            ),
            {
                "tenant_id": args.tenant_id,
                "user_id": args.user_id,
                "embedding_model": settings.rag_embedding_model,
                "samples": sample_count,
            },
        )
        vectors = [str(row[0]) for row in sample_result.all()]
        if not vectors:
            raise RuntimeError("no ready vector can be used as a benchmark query")

        runs = []
        for ef_search in ef_values:
            await session.execute(text(f"SET LOCAL hnsw.ef_search = {ef_search}"))
            await session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
            plans: list[dict[str, Any]] = []
            for vector in vectors:
                explained = await session.execute(
                    text(
                        """EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
                        SELECT c.chunk_uuid
                        FROM paper_chunks c
                        JOIN papers p ON p.paper_uuid=c.paper_uuid
                            AND p.tenant_id=c.tenant_id AND p.user_id=c.user_id
                        WHERE c.tenant_id=:tenant_id AND c.user_id=:user_id
                            AND p.deleted_at IS NULL AND p.in_knowledge_base=true
                            AND c.content_version=p.current_content_version
                            AND c.embedding_status='ready' AND c.embedding IS NOT NULL
                            AND c.embedding_model=:embedding_model
                        ORDER BY c.embedding <=> CAST(:embedding AS vector)
                        LIMIT :result_limit"""
                    ),
                    {
                        "tenant_id": args.tenant_id,
                        "user_id": args.user_id,
                        "embedding_model": settings.rag_embedding_model,
                        "embedding": vector,
                        "result_limit": result_limit,
                    },
                )
                plans.append(_explain_payload(explained.scalar_one()))
            runs.append(
                {
                    "ef_search": ef_search,
                    **summarize_query_plans(plans, index_name=INDEX_NAME),
                }
            )

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tenant_id": args.tenant_id,
        "user_id": args.user_id,
        "embedding_model": settings.rag_embedding_model,
        "vector_count": vector_count,
        "sample_count": len(vectors),
        "result_limit": result_limit,
        "small_corpus_smoke_only": vector_count < int(args.minimum_vectors),
        "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_run(_arguments()))
