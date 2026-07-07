from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services import mysql_store


def now_ms() -> int:
    return int(time.perf_counter() * 1000)


class TraceRecorder:
    def __init__(self, path: Path | None = None) -> None:
        settings = get_settings()
        self.path = path or settings.storage_dir / "trace_events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        trace_id: str,
        span_name: str,
        event_type: str,
        *,
        task_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        latency_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "trace_id": trace_id,
            "task_id": task_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "span_name": span_name,
            "event_type": event_type,
            "provider": provider,
            "model": model,
            "latency_ms": latency_ms,
            "metadata": metadata or {},
        }
        if mysql_store.is_available():
            mysql_store.execute(
                """
                INSERT INTO scholar_trace_events
                    (trace_id, task_id, tenant_id, user_id, span_name, event_type,
                     provider, model, latency_ms, metadata_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    trace_id,
                    task_id,
                    tenant_id,
                    user_id,
                    span_name,
                    event_type,
                    provider,
                    model,
                    latency_ms,
                    mysql_store.encode_json(metadata or {}),
                ),
            )
            return
        line = json.dumps(payload | {"created_at": time.time()}, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


trace_recorder = TraceRecorder()
