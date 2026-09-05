from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import re
from typing import Any, Awaitable, Callable

from agents.factory import model_factory


LIFECYCLE_CAPABILITIES = (
    "literature_retrieval",
    "outline_generation",
    "section_writing",
    "quality_review",
)
CAPABILITY_AGENTS = {
    "literature_retrieval": "retrieval_skill_agent",
    "outline_generation": "outline_skill_agent",
    "section_writing": "section_writing_skill_agent",
    "quality_review": "quality_skill_agent",
}


@dataclass(frozen=True)
class TaskNode:
    node_id: str
    capability: str
    agent_name: str
    instruction: str
    depends_on: tuple[str, ...] = ()
    optional: bool = False
    version: str = "1.0.0"


@dataclass(frozen=True)
class TaskGraphPlan:
    goal: str
    nodes: tuple[TaskNode, ...]
    rationale: tuple[str, ...] = ()
    planner: str = "bounded_fallback"
    plan_version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "rationale": list(self.rationale),
            "planner": self.planner,
            "plan_version": self.plan_version,
            "nodes": [
                {
                    "node_id": node.node_id,
                    "capability": node.capability,
                    "agent_name": node.agent_name,
                    "instruction": node.instruction,
                    "depends_on": list(node.depends_on),
                    "optional": node.optional,
                    "version": node.version,
                }
                for node in self.nodes
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TaskGraphPlan":
        return cls(
            goal=str(payload.get("goal") or ""),
            nodes=tuple(
                TaskNode(
                    node_id=str(item["node_id"]),
                    capability=str(item["capability"]),
                    agent_name=str(item.get("agent_name") or CAPABILITY_AGENTS[str(item["capability"])]),
                    instruction=str(item.get("instruction") or item["capability"]),
                    depends_on=tuple(str(dep) for dep in item.get("depends_on") or ()),
                    optional=bool(item.get("optional", False)),
                    version=str(item.get("version") or "1.0.0"),
                )
                for item in payload.get("nodes") or ()
            ),
            rationale=tuple(str(item) for item in payload.get("rationale") or ()),
            planner=str(payload.get("planner") or "restored"),
            plan_version=str(payload.get("plan_version") or "1.0.0"),
        )

    def node_for_capability(self, capability: str) -> TaskNode:
        return next(node for node in self.nodes if node.capability == capability)

    def descendants(self, node_id: str) -> set[str]:
        selected = {node_id}
        changed = True
        while changed:
            changed = False
            for node in self.nodes:
                if node.node_id not in selected and any(dep in selected for dep in node.depends_on):
                    selected.add(node.node_id)
                    changed = True
        return selected


class DynamicTaskPlanner:
    """Generate a model-authored DAG and constrain it to executable capabilities."""

    def plan_writing(self, goal: str, state: dict[str, Any]) -> TaskGraphPlan:
        previous = str(state.get("retry_target") or "")
        nodes = (
            TaskNode(
                "retrieval",
                "literature_retrieval",
                CAPABILITY_AGENTS["literature_retrieval"],
                "Retrieve, deduplicate and rank evidence for the research goal.",
            ),
            TaskNode(
                "outline",
                "outline_generation",
                CAPABILITY_AGENTS["outline_generation"],
                "Generate an evidence-bound outline and expose it for human confirmation.",
                depends_on=("retrieval",),
            ),
            TaskNode(
                "section_writing",
                "section_writing",
                CAPABILITY_AGENTS["section_writing"],
                "Write independently cacheable sections using only bound source IDs.",
                depends_on=("outline",),
            ),
            TaskNode(
                "quality_review",
                "quality_review",
                CAPABILITY_AGENTS["quality_review"],
                "Evaluate evidence, citations and section quality; return one retry_target.",
                depends_on=("section_writing",),
            ),
        )
        rationale = ["required_research_writing_lifecycle"]
        if previous:
            rationale.append(f"resume_from:{previous}")
        return TaskGraphPlan(goal=goal, nodes=nodes, rationale=tuple(rationale))

    async def plan_writing_with_model(
        self,
        goal: str,
        state: dict[str, Any],
        available_skills: list[dict[str, Any]],
    ) -> TaskGraphPlan:
        fallback = self.plan_writing(goal, state)
        prompt = json.dumps(
            {
                "goal": goal,
                "task_state": {
                    "retrieval_strategy": state.get("retrieval_strategy"),
                    "retrieval_constraints": state.get("retrieval_constraints"),
                    "citation_style": state.get("citation_style"),
                    "max_papers": state.get("max_papers"),
                    "retry_target": state.get("retry_target"),
                    "memory": state.get("memory_context") or state.get("historical_preferences") or {},
                },
                "available_skills": available_skills,
                "allowed_capabilities": list(LIFECYCLE_CAPABILITIES),
                "required_output": {
                    "rationale": ["short reason"],
                    "nodes": [{
                        "node_id": "stable_snake_case_id",
                        "capability": "one allowed capability",
                        "instruction": "specific execution instruction",
                        "depends_on": ["existing node id"],
                        "version": "1.0.0",
                    }],
                },
                "constraints": [
                    "include every allowed capability exactly once",
                    "retrieval must precede outline; outline precedes writing; writing precedes quality",
                    "return JSON only",
                ],
            },
            ensure_ascii=False,
        )
        try:
            response = await model_factory.generate_text(
                "task_graph_planning",
                prompt,
                {
                    "tenant_id": state.get("tenant_id"),
                    "user_id": state.get("user_id"),
                    "task_id": state.get("task_id"),
                    "topic": goal,
                },
            )
            payload = self._json_object(response.content)
            return self._validated_plan(goal, payload, planner=f"model:{response.provider}/{response.model}")
        except Exception as exc:
            return TaskGraphPlan(
                goal=fallback.goal,
                nodes=fallback.nodes,
                rationale=fallback.rationale + (f"planner_fallback:{exc.__class__.__name__}",),
                planner=fallback.planner,
            )

    @staticmethod
    def _json_object(content: str) -> dict[str, Any]:
        match = re.search(r"\{.*\}", content or "", re.DOTALL)
        if not match:
            raise ValueError("planner did not return JSON")
        payload = json.loads(match.group(0))
        if not isinstance(payload, dict):
            raise ValueError("planner JSON must be an object")
        return payload

    def _validated_plan(self, goal: str, payload: dict[str, Any], planner: str) -> TaskGraphPlan:
        raw_nodes = payload.get("nodes")
        if not isinstance(raw_nodes, list):
            raise ValueError("planner nodes must be a list")
        nodes: list[TaskNode] = []
        seen_ids: set[str] = set()
        seen_capabilities: set[str] = set()
        for raw in raw_nodes:
            if not isinstance(raw, dict):
                raise ValueError("planner node must be an object")
            capability = str(raw.get("capability") or "").strip()
            node_id = re.sub(r"[^a-z0-9_]+", "_", str(raw.get("node_id") or capability).lower()).strip("_")
            if capability not in LIFECYCLE_CAPABILITIES or not node_id:
                raise ValueError(f"unsupported lifecycle capability: {capability}")
            if node_id in seen_ids or capability in seen_capabilities:
                raise ValueError("node ids and capabilities must be unique")
            dependencies = tuple(str(item) for item in (raw.get("depends_on") or []))
            if any(item not in seen_ids for item in dependencies):
                raise ValueError("dependencies must reference earlier nodes")
            nodes.append(TaskNode(
                node_id=node_id,
                capability=capability,
                agent_name=CAPABILITY_AGENTS[capability],
                instruction=str(raw.get("instruction") or capability),
                depends_on=dependencies,
                version=str(raw.get("version") or "1.0.0"),
            ))
            seen_ids.add(node_id)
            seen_capabilities.add(capability)
        if seen_capabilities != set(LIFECYCLE_CAPABILITIES):
            raise ValueError("planner omitted a required lifecycle capability")
        by_capability = {node.capability: node for node in nodes}
        expected = {
            "outline_generation": "literature_retrieval",
            "section_writing": "outline_generation",
            "quality_review": "section_writing",
        }
        for capability, dependency_capability in expected.items():
            if by_capability[dependency_capability].node_id not in by_capability[capability].depends_on:
                raise ValueError(f"{capability} must depend on {dependency_capability}")
        rationale = tuple(str(item) for item in (payload.get("rationale") or ["model_generated_plan"]))
        return TaskGraphPlan(goal, tuple(nodes), rationale, planner=planner)


class TaskGraphExecutor:
    def __init__(self, max_parallel: int = 3) -> None:
        self.max_parallel = max(1, max_parallel)

    async def execute(
        self,
        plan: TaskGraphPlan,
        runner: Callable[[TaskNode, dict[str, Any]], Awaitable[Any]],
    ) -> list[Any]:
        pending = {node.node_id: node for node in plan.nodes}
        completed: dict[str, Any] = {}
        ordered: list[Any] = []
        semaphore = asyncio.Semaphore(self.max_parallel)

        async def run_node(node: TaskNode) -> tuple[str, Any]:
            dependencies = {key: completed[key] for key in node.depends_on}
            async with semaphore:
                return node.node_id, await runner(node, dependencies)

        while pending:
            ready = [node for node in pending.values() if all(key in completed for key in node.depends_on)]
            if not ready:
                raise ValueError(f"TaskGraph contains unresolved dependencies: {', '.join(sorted(pending))}")
            for node_id, result in await asyncio.gather(*(run_node(node) for node in ready)):
                completed[node_id] = result
                ordered.append(result)
                pending.pop(node_id, None)
        return ordered


dynamic_task_planner = DynamicTaskPlanner()
task_graph_executor = TaskGraphExecutor()
