from __future__ import annotations

import asyncio

from app.services.repository import task_repository
from app.services.task_service import task_service
from app.schemas import TaskStatus


async def run_pending_once() -> int:
    """Development worker helper for JSON-backed pending tasks."""
    count = 0
    # This is intentionally conservative; production should consume Redis/Celery.
    for tenant_id in {"tenant_demo"}:
        for user_id in {"user_demo"}:
            for item in await task_repository.list_by_user(tenant_id, user_id):
                if item.get("status") == TaskStatus.QUEUED.value:
                    record = await task_repository.get(tenant_id, item["task_id"])
                    if record is not None:
                        await task_service.run_survey_task(record)
                        count += 1
    return count


if __name__ == "__main__":
    print(asyncio.run(run_pending_once()))

