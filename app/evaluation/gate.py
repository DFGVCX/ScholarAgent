from __future__ import annotations

from typing import Any, Mapping

from app.evaluation.production import PRODUCTION_MODES


DEFAULT_THRESHOLDS: dict[str, float] = {
    "recall@10": 0.75,
    "mrr": 0.65,
    "ndcg@3": 0.60,
}


def evaluate_release_gate(
    report: Mapping[str, Any],
    *,
    thresholds: Mapping[str, float] | None = None,
    baseline: Mapping[str, Any] | None = None,
    maximum_metric_drop: float = 0.02,
    allow_degraded: bool = False,
) -> dict[str, Any]:
    failures: list[str] = []
    reports = {
        str(item.get("strategy")): item for item in report.get("reports") or []
    }
    missing = [mode for mode in PRODUCTION_MODES if mode not in reports]
    if missing:
        failures.append("missing strategies: " + ", ".join(missing))
    if not report.get("corpus_fingerprint") or not report.get("query_fingerprint"):
        failures.append("corpus/query fingerprints are required")
    if report.get("corpus_fingerprint_consistent") is False:
        failures.append("corpus fingerprint changed during evaluation")
    consistency = (report.get("runtime_stats") or {}).get("consistency_status")
    if consistency not in {None, "ok"}:
        failures.append(f"vector consistency is {consistency}")

    targets = dict(DEFAULT_THRESHOLDS if thresholds is None else thresholds)
    for mode in PRODUCTION_MODES:
        item = reports.get(mode)
        if not item:
            continue
        if item.get("diagnostic_only"):
            failures.append(f"{mode}: report has no evidence labels")
            continue
        operations = item.get("operations") or {}
        degraded = int(operations.get("degraded_queries") or 0)
        if degraded and not allow_degraded:
            failures.append(f"{mode}: {degraded} degraded queries")
        metrics = item.get("metrics") or {}
        for metric, minimum in targets.items():
            value = float(metrics.get(metric, 0.0))
            if value < float(minimum):
                failures.append(
                    f"{mode}: {metric}={value:.4f} is below {float(minimum):.4f}"
                )

    if baseline:
        baseline_reports = {
            str(item.get("strategy")): item
            for item in baseline.get("reports") or []
        }
        for mode, item in reports.items():
            previous = baseline_reports.get(mode)
            if not previous:
                continue
            for metric in targets:
                current_value = float((item.get("metrics") or {}).get(metric, 0.0))
                baseline_value = float((previous.get("metrics") or {}).get(metric, 0.0))
                drop = baseline_value - current_value
                if drop > maximum_metric_drop:
                    failures.append(
                        f"{mode}: {metric} regressed by {drop:.4f} "
                        f"(allowed {maximum_metric_drop:.4f})"
                    )

    return {
        "passed": not failures,
        "failure_count": len(failures),
        "failures": failures,
        "thresholds": targets,
        "maximum_metric_drop": maximum_metric_drop,
        "allow_degraded": allow_degraded,
    }
