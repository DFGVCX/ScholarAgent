from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.papers.chunking import ChunkDraft, chunk_sections, chunk_text
from app.papers.parsing import ParsedPaper, parse_pdf, parse_pdf_legacy


@dataclass(frozen=True)
class RankingMetrics:
    recall: float
    precision: float
    reciprocal_rank: float
    ndcg: float


def ranking_metrics(
    ranked_ids: Sequence[str],
    relevant_ids: set[str],
    *,
    k: int,
    gains: Mapping[str, float] | None = None,
) -> RankingMetrics:
    limit = max(1, int(k))
    ranked = list(ranked_ids[:limit])
    if not relevant_ids:
        return RankingMetrics(0.0, 0.0, 0.0, 0.0)
    hits = sum(item in relevant_ids for item in ranked)
    recall = hits / len(relevant_ids)
    precision = hits / max(1, len(ranked))
    reciprocal_rank = next(
        (1.0 / rank for rank, item in enumerate(ranked_ids, start=1) if item in relevant_ids),
        0.0,
    )
    relevance = gains or {item: 1.0 for item in relevant_ids}
    dcg = sum(
        (2 ** float(relevance.get(item, 0.0)) - 1) / math.log2(rank + 1)
        for rank, item in enumerate(ranked, start=1)
    )
    ideal = sorted((float(relevance.get(item, 1.0)) for item in relevant_ids), reverse=True)[:limit]
    idcg = sum((2**gain - 1) / math.log2(rank + 1) for rank, gain in enumerate(ideal, start=1))
    return RankingMetrics(recall, precision, reciprocal_rank, dcg / idcg if idcg else 0.0)


def fingerprint_records(records: Iterable[Mapping[str, Any]]) -> str:
    canonical = sorted(
        json.dumps(dict(record), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for record in records
    )
    return hashlib.sha256("\n".join(canonical).encode("utf-8")).hexdigest()


def validate_fingerprints(left: Mapping[str, Any], right: Mapping[str, Any]) -> None:
    labels = {
        "corpus_fingerprint": "corpus fingerprint",
        "query_fingerprint": "query fingerprint",
        "embedding_model": "embedding model",
    }
    for key, label in labels.items():
        if left.get(key) != right.get(key):
            raise ValueError(f"{label} mismatch: {left.get(key)!r} != {right.get(key)!r}")


def build_evaluation_report(
    *,
    strategy: str,
    parser_version: str,
    chunker_version: str,
    embedding_model: str,
    corpus_fingerprint: str,
    query_fingerprint: str,
    query_results: Sequence[Mapping[str, Any]],
    k_values: Sequence[int] = (1, 3, 5, 10),
) -> dict[str, Any]:
    normalized_k = tuple(sorted({max(1, int(value)) for value in k_values}))
    queries = [dict(result) for result in query_results]
    labeled = bool(queries) and all(result.get("relevant_ids") is not None for result in queries)
    report: dict[str, Any] = {
        "strategy": strategy,
        "parser_version": parser_version,
        "chunker_version": chunker_version,
        "embedding_model": embedding_model,
        "corpus_fingerprint": corpus_fingerprint,
        "query_fingerprint": query_fingerprint,
        "diagnostic_only": not labeled,
        "queries": queries,
    }
    if not labeled:
        return report

    metric_rows: dict[int, list[RankingMetrics]] = {value: [] for value in normalized_k}
    for result in queries:
        ranked_ids = [str(item["chunk_id"]) for item in result.get("ranked", [])]
        relevant_ids = {str(item) for item in result.get("relevant_ids") or []}
        gains = {str(key): float(value) for key, value in dict(result.get("gains") or {}).items()}
        result_metrics: dict[str, float] = {}
        for value in normalized_k:
            metrics = ranking_metrics(ranked_ids, relevant_ids, k=value, gains=gains or None)
            metric_rows[value].append(metrics)
            result_metrics[f"recall@{value}"] = metrics.recall
            result_metrics[f"precision@{value}"] = metrics.precision
            result_metrics[f"ndcg@{value}"] = metrics.ndcg
        result_metrics["mrr"] = ranking_metrics(
            ranked_ids, relevant_ids, k=max(len(ranked_ids), 1), gains=gains or None
        ).reciprocal_rank
        result["metrics"] = result_metrics

    count = len(queries)
    aggregate: dict[str, float] = {}
    for value, rows in metric_rows.items():
        aggregate[f"recall@{value}"] = sum(row.recall for row in rows) / count
        aggregate[f"precision@{value}"] = sum(row.precision for row in rows) / count
        aggregate[f"ndcg@{value}"] = sum(row.ndcg for row in rows) / count
    aggregate["mrr"] = sum(float(result["metrics"]["mrr"]) for result in queries) / count
    report["metrics"] = aggregate
    return report


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        records.append(value)
    return records


def _chunks_for_strategy(
    strategy: str,
    paper: Mapping[str, Any],
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> tuple[ParsedPaper, list[ChunkDraft]]:
    path = Path(str(paper["path"])).expanduser().resolve()
    if strategy == "legacy_fixed":
        parsed = parse_pdf_legacy(path)
        chunks = chunk_text(parsed.full_text, chunk_size, chunk_overlap) if parsed.status == "ready" else []
    elif strategy == "structure_aware_v1":
        parsed = parse_pdf(path)
        chunks = chunk_sections(parsed.sections, chunk_size, chunk_overlap) if parsed.status == "ready" else []
    else:
        raise ValueError(f"unsupported strategy: {strategy}")
    return parsed, chunks


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _label_matches(chunk: Mapping[str, Any], label: Mapping[str, Any]) -> bool:
    if label.get("paper_id") and chunk.get("paper_id") != label.get("paper_id"):
        return False
    section_ids = {str(item) for item in label.get("section_ids") or []}
    if section_ids and chunk.get("section_id") not in section_ids:
        return False
    page_ranges = label.get("page_ranges") or []
    if page_ranges:
        start = chunk.get("page_start")
        end = chunk.get("page_end")
        if start is None or end is None:
            return False
        if not any(int(start) <= int(high) and int(end) >= int(low) for low, high in page_ranges):
            return False
    return True


async def evaluate_strategy(
    *,
    strategy: str,
    corpus: Sequence[Mapping[str, Any]],
    queries: Sequence[Mapping[str, Any]],
    embedding_client: Any,
    chunk_size: int = 900,
    chunk_overlap: int = 120,
    top_k: int = 10,
) -> dict[str, Any]:
    chunk_records: list[dict[str, Any]] = []
    parse_failures: list[dict[str, Any]] = []
    for paper in corpus:
        parsed, chunks = _chunks_for_strategy(
            strategy, paper, chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )
        if parsed.status != "ready":
            parse_failures.append(
                {"paper_id": paper.get("paper_id"), "status": parsed.status, "warnings": list(parsed.warnings)}
            )
        for chunk in chunks:
            chunk_records.append(
                {
                    "chunk_id": f"{paper['paper_id']}:{chunk.position}",
                    "paper_id": str(paper["paper_id"]),
                    "title": str(paper.get("title") or paper["paper_id"]),
                    "chunk_index": chunk.position,
                    "section_id": chunk.section_id,
                    "section_path": chunk.section_path,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "content": chunk.content,
                    "embedding_text": chunk.embedding_text(str(paper.get("title") or paper["paper_id"])),
                }
            )
    vectors = await embedding_client.embed([record["embedding_text"] for record in chunk_records]) if chunk_records else []
    for record, vector in zip(chunk_records, vectors):
        record["vector"] = vector

    query_results: list[dict[str, Any]] = []
    query_vectors = await embedding_client.embed([str(query["query"]) for query in queries]) if queries else []
    for query, query_vector in zip(queries, query_vectors):
        ranked_records = sorted(
            chunk_records,
            key=lambda record: (-_cosine(query_vector, record["vector"]), record["chunk_id"]),
        )[: max(1, top_k)]
        ranked = [
            {key: value for key, value in record.items() if key not in {"vector", "embedding_text"}}
            | {"score": _cosine(query_vector, record["vector"])}
            for record in ranked_records
        ]
        relevant_value = query.get("relevant")
        if relevant_value is None:
            relevant_ids = None
        else:
            labels = relevant_value if isinstance(relevant_value, list) else [relevant_value]
            relevant_ids = [
                record["chunk_id"]
                for record in chunk_records
                if any(_label_matches(record, label) for label in labels)
            ]
        query_results.append(
            {"query": str(query["query"]), "ranked": ranked, "relevant_ids": relevant_ids}
        )

    report = build_evaluation_report(
        strategy=strategy,
        parser_version="1",
        chunker_version="1",
        embedding_model=str(embedding_client.model),
        corpus_fingerprint=fingerprint_records(corpus),
        query_fingerprint=fingerprint_records(queries),
        query_results=query_results,
        k_values=tuple(value for value in (1, 3, 5, 10) if value <= max(1, top_k)),
    )
    report["configuration"] = {"chunk_size": chunk_size, "chunk_overlap": chunk_overlap, "top_k": top_k}
    report["corpus"] = {
        "paper_count": len(corpus),
        "chunk_count": len(chunk_records),
        "average_chunk_chars": (
            sum(len(record["content"]) for record in chunk_records) / len(chunk_records)
            if chunk_records else 0.0
        ),
        "parse_failures": parse_failures,
    }
    return report


async def compare_strategies(
    *,
    corpus: Sequence[Mapping[str, Any]],
    queries: Sequence[Mapping[str, Any]],
    embedding_client: Any,
    chunk_size: int = 900,
    chunk_overlap: int = 120,
    top_k: int = 10,
) -> dict[str, Any]:
    reports = []
    for strategy in ("legacy_fixed", "structure_aware_v1"):
        reports.append(
            await evaluate_strategy(
                strategy=strategy,
                corpus=corpus,
                queries=queries,
                embedding_client=embedding_client,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                top_k=top_k,
            )
        )
    validate_fingerprints(reports[0], reports[1])
    return {
        "schema_version": 1,
        "embedding_model": embedding_client.model,
        "corpus_fingerprint": reports[0]["corpus_fingerprint"],
        "query_fingerprint": reports[0]["query_fingerprint"],
        "reports": reports,
    }
