from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDescriptor:
    name: str
    role: str
    description: str
    allowed_skills: tuple[str, ...]
    can_delegate: bool = False
    max_depth: int = 0


class AgentRegistry:
    def __init__(self) -> None:
        self._agents = {
            "general_orchestrator": AgentDescriptor(
                "general_orchestrator", "orchestrator",
                "通用主控 Agent，负责理解任务、选择 Skill、领域 Agent 或子 Agent。",
                ("survey_generation", "knowledge_search", "citation_audit"), True, 1,
            ),
            "writing_agent": AgentDescriptor(
                "writing_agent", "domain_agent",
                "科研写作领域 Agent，拥有写作流程、材料约束和人机协同状态。",
                ("survey_generation", "citation_audit", "citation_format"), True, 1,
            ),
            "research_subagent": AgentDescriptor(
                "research_subagent", "leaf",
                "只负责研究范围、检索词和证据计划。", ("knowledge_search",), False, 0,
            ),
            "structure_subagent": AgentDescriptor(
                "structure_subagent", "leaf",
                "只负责文章结构、章节边界和论证顺序。", (), False, 0,
            ),
            "citation_subagent": AgentDescriptor(
                "citation_subagent", "leaf",
                "只负责引用覆盖和证据风险检查。", ("citation_audit",), False, 0,
            ),
            "critic_subagent": AgentDescriptor(
                "critic_subagent", "leaf",
                "只负责质量反思和缺陷定位。", (), False, 0,
            ),
        }

    def get(self, name: str) -> AgentDescriptor:
        if name not in self._agents:
            raise KeyError(f"unknown agent: {name}")
        return self._agents[name]

    def list(self) -> list[AgentDescriptor]:
        return list(self._agents.values())


agent_registry = AgentRegistry()
