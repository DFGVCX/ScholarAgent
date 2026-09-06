from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


def percentile(samples: Sequence[float], quantile: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(float(value) for value in samples)
    index = max(0, min(len(ordered) - 1, int(math.ceil(quantile * len(ordered))) - 1))
    return round(ordered[index], 3)


def plan_uses_index(plan: Mapping[str, Any], index_name: str) -> bool:
    if str(plan.get("Index Name") or "") == index_name:
        return True
    return any(
        plan_uses_index(child, index_name)
        for child in plan.get("Plans") or []
        if isinstance(child, Mapping)
    )


def plan_buffer_totals(plan: Mapping[str, Any]) -> dict[str, int]:
    totals = {
        "shared_hit_blocks": int(plan.get("Shared Hit Blocks") or 0),
        "shared_read_blocks": int(plan.get("Shared Read Blocks") or 0),
        "temp_read_blocks": int(plan.get("Temp Read Blocks") or 0),
        "temp_written_blocks": int(plan.get("Temp Written Blocks") or 0),
    }
    for child in plan.get("Plans") or []:
        if not isinstance(child, Mapping):
            continue
        nested = plan_buffer_totals(child)
        for key, value in nested.items():
            totals[key] += value
    return totals


def summarize_query_plans(
    plans: Sequence[Mapping[str, Any]], *, index_name: str
) -> dict[str, Any]:
    execution = [float(item.get("Execution Time") or 0.0) for item in plans]
    planning = [float(item.get("Planning Time") or 0.0) for item in plans]
    index_hits = sum(
        plan_uses_index(dict(item.get("Plan") or {}), index_name) for item in plans
    )
    buffers = [plan_buffer_totals(dict(item.get("Plan") or {})) for item in plans]
    return {
        "samples": len(plans),
        "index_name": index_name,
        "index_used_samples": index_hits,
        "index_usage_rate": round(index_hits / len(plans), 6) if plans else 0.0,
        "execution_ms": {
            "p50": percentile(execution, 0.50),
            "p95": percentile(execution, 0.95),
            "p99": percentile(execution, 0.99),
            "average": round(sum(execution) / len(execution), 3) if execution else None,
        },
        "planning_ms": {
            "p95": percentile(planning, 0.95),
            "average": round(sum(planning) / len(planning), 3) if planning else None,
        },
        "buffers": {
            key: sum(row[key] for row in buffers) for key in (
                "shared_hit_blocks",
                "shared_read_blocks",
                "temp_read_blocks",
                "temp_written_blocks",
            )
        },
    }
