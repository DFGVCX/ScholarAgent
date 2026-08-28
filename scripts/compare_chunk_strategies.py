from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.evaluation.retrieval import (
    compare_strategies,
    load_jsonl,
    render_comparison_csv,
    render_comparison_markdown,
    validate_corpus_files,
)
from app.retrieval.embedding import QwenEmbeddingClient


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare legacy and structure-aware paper chunking.")
    parser.add_argument("--corpus-jsonl", type=Path, required=True)
    parser.add_argument("--queries-jsonl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chunk-size", type=int, default=None)
    parser.add_argument("--chunk-overlap", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=10)
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()
    corpus = load_jsonl(args.corpus_jsonl)
    queries = load_jsonl(args.queries_jsonl)
    validate_corpus_files(corpus)
    report = await compare_strategies(
        corpus=corpus,
        queries=queries,
        embedding_client=QwenEmbeddingClient.from_settings(),
        chunk_size=args.chunk_size or settings.rag_chunk_size,
        chunk_overlap=(args.chunk_overlap if args.chunk_overlap is not None else settings.rag_chunk_overlap),
        top_k=max(1, args.top_k),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".csv").write_text(render_comparison_csv(report), encoding="utf-8-sig")
    args.output.with_suffix(".md").write_text(render_comparison_markdown(report), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(_run(_arguments()))
