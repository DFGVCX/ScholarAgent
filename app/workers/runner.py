from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket

from app.config import get_settings
from app.papers.reembedding import embedding_reindex_service
from app.services.repository import task_repository
from app.services.task_queue import task_queue
from app.services.task_service import task_service


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("scholar-worker")


async def _process_one_reembedding(worker_id: str) -> None:
    try:
        result = await embedding_reindex_service.process_next(worker_id)
        if result is not None:
            logger.info(
                "re-embedding job job_id=%s status=%s chunks=%s",
                result.job_id,
                result.status,
                result.chunk_count,
            )
    except Exception:
        logger.exception("re-embedding queue poll failed")


async def run_worker() -> None:
    settings = get_settings()
    if not task_queue.enabled():
        raise RuntimeError(
            "Worker requires SCHOLAR_TASK_EXECUTION_MODE=queue and SCHOLAR_REDIS_URL"
        )
    if not await task_queue.health():
        raise RuntimeError("Redis task queue is unavailable")
    recovered = await task_queue.recover_processing()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(event, stop.set)
        except NotImplementedError:
            pass

    logger.info("worker started queue=%s recovered=%s", settings.task_queue_name, recovered)
    while not stop.is_set():
        reserved = await task_queue.reserve(timeout=3)
        if reserved is None:
            await _process_one_reembedding(worker_id)
            continue
        raw, payload = reserved
        try:
            record = await task_repository.get(
                str(payload["tenant_id"]), str(payload["task_id"])
            )
            if record is None or record.user_id != str(payload["user_id"]):
                logger.warning("discarding missing or tenant-mismatched task")
                await task_queue.acknowledge(raw)
                continue
            await task_service.run_survey_task(record)
            await task_queue.acknowledge(raw)
            await _process_one_reembedding(worker_id)
        except Exception:
            logger.exception("task execution failed task_id=%s", payload.get("task_id"))
            if int(payload.get("attempt") or 0) + 1 < settings.task_max_attempts:
                await task_queue.retry(raw, payload)
                await asyncio.sleep(1)
            else:
                await task_queue.acknowledge(raw)
    await task_queue.close()


if __name__ == "__main__":
    asyncio.run(run_worker())

