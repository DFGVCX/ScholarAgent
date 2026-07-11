from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from agents.factory import model_factory
from agents.registry import agent_registry
from app.schemas import UserContext
from app.services import mysql_store


@dataclass(frozen=True)
class DelegationResult:
    run_id: str
    agent_name: str
    status: str
    content: str
    latency_ms: int
    error: str = ""


class DelegationService:
    def __init__(self, max_children: int = 3, timeout_seconds: float = 75.0) -> None:
        self.max_children = max_children
        self.timeout_seconds = timeout_seconds

    def start_parent(
        self, user: UserContext, *, agent_name: str, goal: str,
        conversation_id: str = "", task_id: str = "", payload: dict[str, Any] | None = None,
    ) -> str:
        descriptor = agent_registry.get(agent_name)
        if not descriptor.can_delegate:
            raise ValueError("parent agent must support delegation")
        run_id = f"agent_run_{uuid.uuid4().hex}"
        mysql_store.execute(
            "INSERT INTO scholar_agent_runs (run_id,parent_run_id,conversation_id,task_id,tenant_id,user_id,"
            "agent_name,agent_role,execution_mode,goal,status,depth,input_json,result_json,error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, None, conversation_id or None, task_id or None, user.tenant_id, user.user_id,
             descriptor.name, descriptor.role, "delegation", goal, "running", 0,
             json.dumps(payload or {}, ensure_ascii=False), "{}", ""),
        )
        return run_id

    def finish_parent(
        self, user: UserContext, run_id: str, *, status: str,
        result: dict[str, Any] | None = None, error: str = "",
    ) -> None:
        self._finish_run(user, run_id, status, result or {}, error)

    async def run_batch(
        self, user: UserContext, *, parent_run_id: str, goal: str,
        assignments: list[dict[str, Any]], conversation_id: str = "", task_id: str = "",
    ) -> list[DelegationResult]:
        selected = assignments[: self.max_children]
        return list(await asyncio.gather(*[
            self._run_child(
                user, parent_run_id=parent_run_id, goal=goal,
                agent_name=str(item["agent_name"]), instruction=str(item["instruction"]),
                context=dict(item.get("context") or {}), conversation_id=conversation_id, task_id=task_id,
            )
            for item in selected
        ]))

    async def _run_child(
        self, user: UserContext, *, parent_run_id: str, goal: str,
        agent_name: str, instruction: str, context: dict[str, Any],
        conversation_id: str, task_id: str,
    ) -> DelegationResult:
        descriptor = agent_registry.get(agent_name)
        if descriptor.can_delegate:
            raise ValueError("child agent cannot be an orchestrator")
        run_id = f"agent_run_{uuid.uuid4().hex}"
        started = time.perf_counter()
        self._create_run(user, run_id, parent_run_id, descriptor.name, descriptor.role, goal,
                         conversation_id, task_id, {"instruction": instruction, "context": context})
        try:
            prompt = (
                f"你是 ScholarAgent 的受限子 Agent：{descriptor.description}\n"
                "你不能继续委派，也不能执行未授权写操作。只完成分配的子任务，返回简洁、结构化、可供主 Agent 汇总的结果。\n\n"
                f"总目标：{goal}\n子任务：{instruction}\n上下文：{json.dumps(context, ensure_ascii=False)[:6000]}"
            )
            response = await asyncio.wait_for(
                model_factory.generate_text(
                    f"subagent_{agent_name}", prompt,
                    {"tenant_id": user.tenant_id, "user_id": user.user_id, "task_id": task_id},
                ),
                timeout=self.timeout_seconds,
            )
            latency = int((time.perf_counter() - started) * 1000)
            self._finish_run(user, run_id, "succeeded", {"content": response.content, "model": response.model}, "")
            return DelegationResult(run_id, agent_name, "succeeded", response.content, latency)
        except Exception as exc:
            latency = int((time.perf_counter() - started) * 1000)
            self._finish_run(user, run_id, "failed", {}, str(exc))
            return DelegationResult(run_id, agent_name, "failed", "", latency, str(exc))

    @staticmethod
    def _create_run(user: UserContext, run_id: str, parent_run_id: str, agent_name: str,
                    role: str, goal: str, conversation_id: str, task_id: str, payload: dict[str, Any]) -> None:
        mysql_store.execute(
            "INSERT INTO scholar_agent_runs (run_id,parent_run_id,conversation_id,task_id,tenant_id,user_id,"
            "agent_name,agent_role,execution_mode,goal,status,depth,input_json,result_json,error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id,parent_run_id,conversation_id or None,task_id or None,user.tenant_id,user.user_id,
             agent_name,role,"delegated",goal,"running",1,json.dumps(payload,ensure_ascii=False),"{}",""),
        )

    @staticmethod
    def _finish_run(user: UserContext, run_id: str, status: str, result: dict[str, Any], error: str) -> None:
        mysql_store.execute(
            "UPDATE scholar_agent_runs SET status=?,result_json=?,error=?,completed_at=CURRENT_TIMESTAMP "
            "WHERE tenant_id=? AND user_id=? AND run_id=?",
            (status,json.dumps(result,ensure_ascii=False),error,user.tenant_id,user.user_id,run_id),
        )


delegation_service = DelegationService()
