from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.db.session import tenant_transaction
from app.papers.repository import PaperRepository


logger = logging.getLogger(__name__)


async def persist_embedding_usage(
    tenant_id: str,
    user_id: str,
    client: Any,
    *,
    operation: str,
    timeout_seconds: float = 0.25,
) -> bool:
    """Persist one completed client call without affecting the RAG result.

    Usage telemetry gets its own transaction. A metrics write failure must not
    roll back parsed content, vectors, or a lexical fallback response.
    """

    usage = getattr(client, "last_usage", None)
    if usage is None:
        return False
    async def write_event() -> None:
        async with tenant_transaction(tenant_id, user_id) as session:
            await PaperRepository(session).record_embedding_usage(
                tenant_id,
                user_id,
                operation=operation,
                model=usage.model,
                status=usage.status,
                input_count=usage.input_count,
                request_count=usage.request_count,
                successful_request_count=usage.successful_request_count,
                failed_request_count=usage.failed_request_count,
                cancelled_request_count=usage.cancelled_request_count,
                reported_tokens=usage.reported_tokens,
                usage_reported_requests=usage.usage_reported_requests,
                successful_usage_reported_requests=(
                    usage.successful_usage_reported_requests
                ),
                duration_ms=usage.duration_ms,
                error_type=usage.error_type,
            )

    task = asyncio.create_task(write_event())
    try:
        done, _ = await asyncio.wait(
            {task}, timeout=max(0.01, float(timeout_seconds))
        )
        if not done:
            task.cancel()

            def consume_cancelled(completed: asyncio.Task[None]) -> None:
                try:
                    completed.exception()
                except asyncio.CancelledError:
                    pass

            task.add_done_callback(consume_cancelled)
            logger.warning("Embedding usage telemetry write timed out")
            return False
        await task
        return True
    except asyncio.CancelledError:
        task.cancel()
        raise
    except Exception as exc:
        logger.warning("Embedding usage telemetry write failed: %s", type(exc).__name__)
        return False
