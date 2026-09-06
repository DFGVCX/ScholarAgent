from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.evaluation.production import (
    evaluate_production_retrieval,
    render_production_report,
)
from app.evaluation.retrieval import load_jsonl


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate lexical, vector, RRF and reranked retrieval through the production PostgreSQL chain."
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--queries-jsonl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--probe-k", type=int, default=50)
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    from app.services.rag_service import rag_service

    queries = load_jsonl(args.queries_jsonl)

    async def compare(query: str, limit: int):
        return await rag_service.compare(
            args.tenant_id, args.user_id, query, limit
        )

    stats = await rag_service.stats(args.tenant_id, args.user_id)
    report = await evaluate_production_retrieval(
        queries=queries,
        compare_search=compare,
        top_k=args.top_k,
        probe_k=args.probe_k,
        runtime_stats=stats,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.output.with_suffix(".md").write_text(
        render_production_report(report), encoding="utf-8"
    )


if __name__ == "__main__":
    asyncio.run(_run(_arguments()))
