from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class TaskNode:
    node_id: str
    capability: str
    agent_name: str
    instruction: str
    depends_on: tuple[str, ...] = ()
    optional: bool = False


@dataclass(frozen=True)
class TaskGraphPlan:
    goal: str
    nodes: tuple[TaskNode, ...]
    rationale: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "rationale": list(self.rationale),
            "nodes": [
                {
                    "node_id": node.node_id,
                    "capability": node.capability,
                    "agent_name": node.agent_name,
                    "instruction": node.instruction,
                    "depends_on": list(node.depends_on),
                    "optional": node.optional,
                }
                for node in self.nodes
            ],
        }


class DynamicTaskPlanner:
    """Build a bounded DAG; simple requests stay on the direct Skill path."""

    def plan_writing(self, goal: str, state: dict[str, Any]) -> TaskGraphPlan:
        if not goal.strip():
            return TaskGraphPlan(goal=goal, nodes=(), rationale=("empty_goal",))
        nodes: list[TaskNode] = [
            TaskNode("research_scope", "literature_retrieval", "research_subagent",
                     "Define retrieval scope, query variants, evidence priority and exclusions."),
            TaskNode("argument_structure", "outline", "structure_subagent",
                     "Define section boundaries, argument order and human review points.",
                     depends_on=("research_scope",)),
        ]
        rationale = ["writing_requires_research_and_structure"]
        lowered = goal.lower()
        if state.get("citation_style") or any(word in lowered for word in ("citation", "reference", "paper", "survey")):
            nodes.append(TaskNode(
                "citation_policy", "citation_audit", "citation_subagent",
                "Define citation coverage targets and evidence-risk checks.",
                depends_on=("research_scope",),
            ))
            rationale.append("citation_constraints_present")
        if len(goal) >= 180 or int(state.get("max_papers") or 0) >= 30:
            nodes.append(TaskNode(
                "preflight_critic", "quality_review", "critic_subagent",
                "Review the plan for missing constraints, contradictions and unverifiable claims.",
                depends_on=tuple(node.node_id for node in nodes), optional=True,
            ))
            rationale.append("high_complexity_preflight_review")
        return TaskGraphPlan(goal=goal, nodes=tuple(nodes), rationale=tuple(rationale))


class TaskGraphExecutor:
    """Execute ready nodes by dependency wave with bounded concurrency."""

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
            ready = [node for node in pending.values()
                     if all(key in completed for key in node.depends_on)]
            if not ready:
                unresolved = ", ".join(sorted(pending))
                raise ValueError(f"TaskGraph contains a cycle or missing dependency: {unresolved}")
            for node_id, result in await asyncio.gather(*(run_node(node) for node in ready)):
                completed[node_id] = result
                ordered.append(result)
                pending.pop(node_id, None)
        return ordered


dynamic_task_planner = DynamicTaskPlanner()
task_graph_executor = TaskGraphExecutor()
