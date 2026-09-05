from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
import hashlib
import json
from typing import Any, Mapping, Sequence


RETRIEVAL_ALGORITHM_VERSION = "retrieval-v3"


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (set, tuple)):
        return list(value)
    return str(value)


def stable_fingerprint(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def candidate_fingerprint(candidates: Sequence[Any]) -> str:
    return stable_fingerprint(
        [
            {
                "chunk_id": str(item.chunk_id),
                "paper_id": str(item.paper_id),
                "content_version": int(item.content_version),
                "content_hash": hashlib.sha256(
                    str(item.content).encode("utf-8")
                ).hexdigest(),
                "source_score": round(float(item.score), 12),
            }
            for item in candidates
        ]
    )


def result_fingerprint(hits: Sequence[Any]) -> str:
    return stable_fingerprint(
        [
            {
                "chunk_id": str(item.chunk_id),
                "content_version": int(item.content_version),
                "final_rank": int(item.final_rank),
                "rrf_score": round(float(item.rrf_score), 12),
                "rerank_score": (
                    round(float(item.rerank_score), 12)
                    if item.rerank_score is not None
                    else None
                ),
            }
            for item in hits
        ]
    )


def retrieval_provenance(
    *,
    query: str,
    filters: Mapping[str, Any],
    requested_mode: str,
    effective_mode: str,
    candidate_limit: int,
    result_limit: int,
    max_chunks_per_paper: int,
    embedding_model: str,
    reranker_model: str | None,
    corpus_fingerprint: str,
    lexical_candidates: Sequence[Any],
    vector_candidates: Sequence[Any],
    hits: Sequence[Any],
) -> dict[str, Any]:
    configuration = {
        "requested_mode": requested_mode,
        "effective_mode": effective_mode,
        "candidate_limit": candidate_limit,
        "result_limit": result_limit,
        "max_chunks_per_paper": max_chunks_per_paper,
        "embedding_model": embedding_model,
        "reranker_model": reranker_model,
        "algorithm_version": RETRIEVAL_ALGORITHM_VERSION,
    }
    return {
        "schema_version": 1,
        "algorithm_version": RETRIEVAL_ALGORITHM_VERSION,
        "corpus_fingerprint": corpus_fingerprint,
        "query_fingerprint": stable_fingerprint(
            {"query": query, "filters": dict(filters)}
        ),
        "strategy_fingerprint": stable_fingerprint(configuration),
        "candidate_fingerprints": {
            "lexical": candidate_fingerprint(lexical_candidates),
            "vector": candidate_fingerprint(vector_candidates),
        },
        "result_fingerprint": result_fingerprint(hits),
        "configuration": configuration,
    }
