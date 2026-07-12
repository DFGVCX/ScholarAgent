from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services import mysql_store


def now_ms() -> int:
    return int(time.perf_counter() * 1000)


class TraceRecorder:
    def __init__(self, path: Path | None = None, langfuse_client: Any | None = None) -> None:
        settings = get_settings()
        self.path = path or settings.storage_dir / "trace_events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._langfuse = langfuse_client
        self._langfuse_error = ""
        if self._langfuse is None and settings.langfuse_enabled:
            if settings.langfuse_public_key and settings.langfuse_secret_key:
                try:
                    from langfuse import Langfuse

                    self._langfuse = Langfuse(
                        public_key=settings.langfuse_public_key,
                        secret_key=settings.langfuse_secret_key,
                        base_url=settings.langfuse_base_url,
                        environment=settings.langfuse_environment,
                        tracing_enabled=True,
                    )
                except Exception as exc:
                    self._langfuse_error = str(exc)
            else:
                self._langfuse_error = "Langfuse is enabled but credentials are incomplete"

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
        else:
            line = json.dumps(payload | {"created_at": time.time()}, ensure_ascii=False)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        self._record_langfuse(payload)

    def _record_langfuse(self, payload: dict[str, Any]) -> None:
        if self._langfuse is None:
            return
        try:
            event_type = str(payload.get("event_type") or "")
            observation_type = {
                "model_call": "generation",
                "tool_call": "tool",
                "retrieval": "retriever",
                "workflow": "chain",
                "agent": "agent",
                "evaluation": "evaluator",
            }.get(event_type, "span")
            metadata = self._sanitize(payload.get("metadata") or {})
            trace_id = self._langfuse.create_trace_id(seed=str(payload["trace_id"]))
            observation = self._langfuse.start_observation(
                trace_context={"trace_id": trace_id},
                name=str(payload["span_name"]),
                as_type=observation_type,
                output=metadata,
                metadata={
                    "scholar_trace_id": payload["trace_id"],
                    "task_id": payload.get("task_id"),
                    "tenant_id": payload.get("tenant_id"),
                    "user_id": payload.get("user_id"),
                    "provider": payload.get("provider"),
                    "latency_ms": payload.get("latency_ms"),
                },
                model=payload.get("model") if observation_type == "generation" else None,
                level="ERROR" if event_type in {"error", "failed"} else "DEFAULT",
            )
            observation.end()
            self._langfuse_error = ""
        except Exception as exc:
            self._langfuse_error = str(exc)

    @classmethod
    def _sanitize(cls, value: Any) -> Any:
        sensitive = re.compile(r"(api.?key|authorization|token|secret|password)", re.IGNORECASE)
        if isinstance(value, dict):
            return {
                str(key): "[REDACTED]" if sensitive.search(str(key)) else cls._sanitize(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._sanitize(item) for item in value[:100]]
        if isinstance(value, str):
            return value[:8000]
        return value

    def status(self) -> dict[str, Any]:
        settings = get_settings()
        return {
            "enabled": settings.langfuse_enabled,
            "configured": bool(settings.langfuse_public_key and settings.langfuse_secret_key),
            "active": self._langfuse is not None,
            "base_url": settings.langfuse_base_url,
            "last_error": self._langfuse_error,
        }

    def flush(self) -> None:
        if self._langfuse is not None:
            self._langfuse.flush()


trace_recorder = TraceRecorder()
