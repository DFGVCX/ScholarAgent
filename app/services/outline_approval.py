from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass
class OutlineDecision:
    approved: bool
    comment: str = ""
    outline_markdown: str = ""


class OutlineApprovalRegistry:
    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}
        self._decisions: dict[str, OutlineDecision] = {}
        self._outlines: dict[str, dict[str, Any]] = {}

    def open(self, task_id: str, outline_payload: dict[str, Any]) -> None:
        self._events[task_id] = asyncio.Event()
        self._outlines[task_id] = outline_payload

    def approve(self, task_id: str, comment: str = "", outline_markdown: str = "") -> bool:
        event = self._events.get(task_id)
        if event is None:
            return False
        self._decisions[task_id] = OutlineDecision(
            approved=True,
            comment=comment,
            outline_markdown=outline_markdown,
        )
        event.set()
        return True

    async def wait(self, task_id: str, timeout_seconds: float = 1800) -> OutlineDecision:
        event = self._events.get(task_id)
        if event is None:
            raise RuntimeError("Outline approval is not pending")
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout_seconds)
            return self._decisions.get(task_id, OutlineDecision(approved=False, comment="No approval decision"))
        finally:
            self._events.pop(task_id, None)
            self._decisions.pop(task_id, None)
            self._outlines.pop(task_id, None)

    def pending_payload(self, task_id: str) -> dict[str, Any] | None:
        return self._outlines.get(task_id)


outline_approval_registry = OutlineApprovalRegistry()
