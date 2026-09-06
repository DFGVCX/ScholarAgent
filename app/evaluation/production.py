from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import math
from typing import Any, Awaitable, Callable, Mapping, Sequence

from app.evaluation.retrieval import (
    _label_matches,
    build_evaluation_report,
    fingerprint_records,
)


PRODUCTION_MODES = ("lexical", "vector", "hybrid", "hybrid_rerank")


def _evidence_labels(query: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = query.get("relevant")
    values = raw if isinstance(raw, list) else ([raw] if isinstance(raw, Mapping) else [])
    labels: list[dict[str, Any]] = []
    for index, value in enumerate(values, start=1):
        label = dict(value)
        label["evidence_id"] = str(
            label.get("evidence_id")
            or f"{query.get('query_id') or query.get('query')}:e{index}"
        )
        labels.append(label)
    return labels


def _annotated_hits(
    hits: Sequence[Mapping[str, Any]], labels: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    return [
        {
            **dict(hit),
            "matched_evidence_ids": [
                str(label["evidence_id"])
                for label in labels
                if _label_matches(hit, label)
            ],
        }
        for hit in hits
    ]


def classify_retrieval_failure(
    top_hits: Sequence[Mapping[str, Any]],
    probe_hits: Sequence[Mapping[str, Any]],
    labels: Sequence[Mapping[str, Any]],
) -> str:
    if any(hit.get("matched_evidence_ids") for hit in top_hits):
        return "passed"
    if any(hit.get("matched_evidence_ids") for hit in probe_hits):
        return "ranked_too_low"
    expected_papers = {str(label.get("paper_id")) for label in labels if label.get("paper_id")}
    if not probe_hits:
        return "not_retrieved"
    if expected_papers and not any(
        str(hit.get("paper_id")) in expected_papers for hit in probe_hits
    ):
        return "paper_not_retrieved"
    return "evidence_not_retrieved"


def _aggregate_group(
    rows: Sequence[Mapping[str, Any]], *, mode: str, group_name: str
) -> dict[str, Any]:
    report = build_evaluation_report(
        strategy=mode,
        parser_version="runtime",
        chunker_version="runtime",
        embedding_model="runtime",
        corpus_fingerprint="runtime",
        query_fingerprint=fingerprint_records(rows),
        query_results=rows,
        k_values=(1, 3, 5, 10),
    )
    return {
        "group": group_name,
        "query_count": len(rows),
        "metrics": report.get("metrics") or {},
    }


async def evaluate_production_retrieval(
    *,
    queries: Sequence[Mapping[str, Any]],
    compare_search: Callable[[str, int], Awaitable[Mapping[str, Any]]],
    top_k: int = 10,
    probe_k: int = 50,
    runtime_stats: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    top_k = max(1, min(int(top_k), 50))
    probe_k = max(top_k, min(int(probe_k), 50))
    per_mode: dict[str, list[dict[str, Any]]] = {mode: [] for mode in PRODUCTION_MODES}
    failures: dict[str, Counter[str]] = {mode: Counter() for mode in PRODUCTION_MODES}
    latencies: dict[str, list[float]] = defaultdict(list)
    context_chars: dict[str, list[int]] = defaultdict(list)
    degradation_count: Counter[str] = Counter()
    corpus_fingerprints: set[str] = set()

    for query in queries:
        query_text = str(query.get("query") or "").strip()
        if not query_text:
            raise ValueError("every production evaluation query must contain query")
        labels = _evidence_labels(query)
        if not labels:
            raise ValueError(f"query {query.get('query_id') or query_text!r} has no evidence labels")
        comparison = await compare_search(query_text, probe_k)
        strategies = comparison.get("strategies") or {}
        for mode in PRODUCTION_MODES:
            result = dict(strategies.get(mode) or {})
            probe_hits = _annotated_hits(result.get("items") or [], labels)
            top_hits = probe_hits[:top_k]
            failure = classify_retrieval_failure(top_hits, probe_hits, labels)
            failures[mode][failure] += 1
            warnings = list(result.get("warnings") or [])
            requested = str((result.get("ranking_policy") or {}).get("requested_mode") or mode)
            effective = str(result.get("retrieval_mode") or "")
            degraded = bool(warnings) or effective != requested
            if degraded:
                degradation_count[mode] += 1
            elapsed = (result.get("debug") or {}).get("timings_ms", {}).get("total_ms")
            if isinstance(elapsed, (int, float)):
                latencies[mode].append(float(elapsed))
            context_chars[mode].append(
                sum(len(str(hit.get("snippet") or hit.get("content") or "")) for hit in top_hits)
            )
            reproducibility = dict(result.get("reproducibility") or {})
            if reproducibility.get("corpus_fingerprint"):
                corpus_fingerprints.add(str(reproducibility["corpus_fingerprint"]))
            per_mode[mode].append(
                {
                    "query_id": query.get("query_id"),
                    "query": query_text,
                    "language": str(query.get("language") or "unknown"),
                    "category": str(query.get("category") or "unknown"),
                    "ranked": top_hits,
                    "evidence_ids": [str(label["evidence_id"]) for label in labels],
                    "evidence_labels": labels,
                    "failure_class": failure,
                    "requested_mode": requested,
                    "effective_mode": effective,
                    "degraded": degraded,
                    "warnings": warnings,
                    "reproducibility": reproducibility,
                }
            )

    reports: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    corpus_fingerprint = (
        next(iter(corpus_fingerprints))
        if len(corpus_fingerprints) == 1
        else fingerprint_records([{"value": value} for value in sorted(corpus_fingerprints)])
    )
    query_fingerprint = fingerprint_records(queries)
    for mode, rows in per_mode.items():
        report = build_evaluation_report(
            strategy=mode,
            parser_version="runtime",
            chunker_version="runtime",
            embedding_model=str((runtime_stats or {}).get("embedding_model") or "runtime"),
            corpus_fingerprint=corpus_fingerprint,
            query_fingerprint=query_fingerprint,
            query_results=rows,
            k_values=tuple(value for value in (1, 3, 5, 10) if value <= top_k),
        )
        samples = sorted(latencies[mode])
        report["operations"] = {
            "failure_classes": dict(failures[mode]),
            "degraded_queries": degradation_count[mode],
            "average_latency_ms": round(sum(samples) / len(samples), 3) if samples else None,
            "p95_latency_ms": _percentile(samples, 0.95),
            "average_context_chars": round(
                sum(context_chars[mode]) / len(context_chars[mode]), 2
            ) if context_chars[mode] else 0.0,
            "average_context_tokens_estimated": round(
                sum(context_chars[mode]) / max(1, len(context_chars[mode])) / 4,
                2,
            ),
        }
        reports.append(report)
        grouped[mode] = {}
        for dimension in ("language", "category"):
            buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                buckets[str(row.get(dimension) or "unknown")].append(row)
            grouped[mode][dimension] = [
                _aggregate_group(values, mode=mode, group_name=name)
                for name, values in sorted(buckets.items())
            ]

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "query_count": len(queries),
        "top_k": top_k,
        "probe_k": probe_k,
        "corpus_fingerprint": corpus_fingerprint,
        "query_fingerprint": query_fingerprint,
        "corpus_fingerprint_consistent": len(corpus_fingerprints) <= 1,
        "strategy_order": list(PRODUCTION_MODES),
        "reports": reports,
        "grouped_metrics": grouped,
        "runtime_stats": dict(runtime_stats or {}),
    }


def _percentile(samples: Sequence[float], quantile: float) -> float | None:
    if not samples:
        return None
    index = max(0, min(len(samples) - 1, int(math.ceil(quantile * len(samples))) - 1))
    return round(float(samples[index]), 3)


def render_production_report(report: Mapping[str, Any]) -> str:
    lines = [
        "# RAG 生产检索评测",
        "",
        f"- 查询数：{report.get('query_count', 0)}",
        f"- Top-K：{report.get('top_k', 0)}",
        f"- 语料指纹：`{report.get('corpus_fingerprint') or '-'}`",
        f"- 查询集指纹：`{report.get('query_fingerprint') or '-'}`",
        "",
        "| 策略 | Recall@10 | MRR | NDCG@3 | 平均延迟(ms) | P95(ms) | 降级查询 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in report.get("reports") or []:
        metrics = item.get("metrics") or {}
        operations = item.get("operations") or {}
        lines.append(
            "| {strategy} | {recall:.4f} | {mrr:.4f} | {ndcg:.4f} | {avg} | {p95} | {degraded} |".format(
                strategy=item.get("strategy") or "-",
                recall=float(metrics.get("recall@10", metrics.get("recall@5", 0.0))),
                mrr=float(metrics.get("mrr", 0.0)),
                ndcg=float(metrics.get("ndcg@3", 0.0)),
                avg=operations.get("average_latency_ms"),
                p95=operations.get("p95_latency_ms"),
                degraded=operations.get("degraded_queries", 0),
            )
        )
    lines.extend(["", "失败分类和逐查询证据匹配结果见同名 JSON 文件。", ""])
    return "\n".join(lines)
