from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agents.delegation import delegation_service
from agents.factory import model_factory
from app.schemas import UserContext
from app.services.conversation_state_service import conversation_state_service


WRITING_WORDS = ("写作", "论文", "综述", "报告", "章节", "大纲", "参考文献", "改写", "续写")
AUDIT_WORDS = ("引用审计", "引用核验", "虚构引用", "引用覆盖", "可追溯")
RETRIEVAL_WORDS = ("检索", "搜索", "查找", "知识库", "文献", "论文")
COMPLEX_WORDS = ("对比", "分析", "规划", "系统", "完整", "多角度", "分别", "评估", "方案", "端到端")


@dataclass(frozen=True)
class RouteDecision:
    target_agent: str
    execution_mode: str
    complexity: int
    reasons: tuple[str, ...]
    intent: str = "general_chat"
    confidence: float = 0.8
    required_capabilities: tuple[str, ...] = ()
    planned_steps: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_agent": self.target_agent,
            "execution_mode": self.execution_mode,
            "complexity": self.complexity,
            "reasons": list(self.reasons),
            "intent": self.intent,
            "confidence": self.confidence,
            "required_capabilities": list(self.required_capabilities),
            "planned_steps": list(self.planned_steps),
        }


class GeneralOrchestratorAgent:
    """State-aware router. Tools run first; this router handles non-tool Agent work."""

    def decide(
        self,
        content: str,
        skill_id: str = "general_assistant",
        working_state: dict[str, Any] | None = None,
    ) -> RouteDecision:
        state = working_state or {}
        score = 0
        reasons: list[str] = []
        capabilities: list[str] = []
        steps: list[str] = []

        if len(content) > 180:
            score += 2
            reasons.append("long_request")
        constraint_count = sum(content.count(mark) for mark in ("，", "；", "\n", "并且", "然后"))
        if constraint_count >= 4:
            score += 2
            reasons.append("multi_constraint")
        complex_hits = sum(1 for word in COMPLEX_WORDS if word in content)
        if complex_hits >= 2:
            score += 2
            reasons.append("multi_stage_reasoning")
        if re.search(r"(?:第一|第二|1[.、]|2[.、])", content):
            score += 1
            reasons.append("explicit_steps")

        writing = skill_id == "survey_review" or any(word in content for word in WRITING_WORDS)
        audit = skill_id == "citation_audit" or any(word in content for word in AUDIT_WORDS)
        retrieval = skill_id == "knowledge_base" or any(word in content for word in RETRIEVAL_WORDS)

        if writing:
            intent = "academic_writing"
            target = "writing_agent"
            capabilities.extend(("outline", "literature_retrieval", "section_writing", "quality_review"))
            steps.extend(("clarify_scope", "route_writing_skill", "review_output"))
            reasons.append("writing_intent")
        elif audit:
            intent = "citation_audit"
            target = "citation_agent"
            capabilities.extend(("citation_binding", "source_verification"))
            steps.extend(("load_task_evidence", "verify_citations", "report_findings"))
            reasons.append("citation_audit_intent")
        elif retrieval:
            intent = "knowledge_retrieval"
            target = "research_agent"
            capabilities.extend(("hybrid_retrieval", "source_ranking"))
            steps.extend(("resolve_source", "retrieve", "synthesize"))
            reasons.append("retrieval_intent")
        else:
            intent = "general_chat"
            target = "general_orchestrator"
            capabilities.append("conversation")
            steps.append("respond")
            reasons.append("general_intent")

        if state.get("active_source"):
            reasons.append(f"active_source:{state['active_source']}")
        if state.get("pending_action"):
            reasons.append("pending_action_present")

        domain_task = target != "general_orchestrator"
        execution_mode = (
            "delegation"
            if score >= 4 or (domain_task and score >= 2)
            else ("skill" if domain_task else "direct")
        )
        confidence = 0.96 if target != "general_orchestrator" else 0.82
        return RouteDecision(
            target, execution_mode, score, tuple(dict.fromkeys(reasons)), intent,
            confidence, tuple(dict.fromkeys(capabilities)), tuple(steps),
        )

    async def execute_complex(
        self,
        user: UserContext,
        conversation_id: str,
        content: str,
        context_prompt: str,
    ) -> tuple[str, dict[str, Any]]:
        state = conversation_state_service.get(user, conversation_id)
        decision = self.decide(content, working_state=state)
        route_state = conversation_state_service.record_route(
            user, conversation_id,
            intent=decision.intent,
            target=decision.target_agent,
            execution_mode="delegation",
            reasons=list(decision.reasons),
            confidence=decision.confidence,
            planned_steps=list(decision.planned_steps),
        )
        parent_run_id = delegation_service.start_parent(
            user,
            agent_name="general_orchestrator",
            goal=content,
            conversation_id=conversation_id,
            payload={"context": context_prompt[-6000:], "working_state": state},
        )
        assignments = [
            {
                "agent_name": "research_subagent",
                "instruction": "拆解问题，给出事实、检索维度和可验证证据。",
                "context": {"conversation": context_prompt, "working_state": state},
            },
            {
                "agent_name": "critic_subagent",
                "instruction": "从风险、遗漏、约束冲突和可验证性角度审查需求。",
                "context": {"conversation": context_prompt, "working_state": state},
            },
        ]
        children = await delegation_service.run_batch(
            user,
            parent_run_id=parent_run_id,
            goal=content,
            assignments=assignments,
            conversation_id=conversation_id,
        )
        evidence = "\n\n".join(
            f"[{item.agent_name}/{item.status}]\n{item.content or item.error}" for item in children
        )
        try:
            response = await model_factory.generate_text(
                "orchestrator_synthesis",
                f"用户任务：{content}\n\n会话上下文：{context_prompt}\n\n"
                f"子 Agent 结果：{evidence}\n\n"
                "请由主控 Agent 汇总为一致答案，不虚构子 Agent 未提供的事实。",
                {"tenant_id": user.tenant_id, "user_id": user.user_id, "working_state": state},
            )
            result = {"children": [item.__dict__ for item in children], "model": response.model}
            delegation_service.finish_parent(user, parent_run_id, status="succeeded", result=result)
            return response.content, {
                "kind": "agent_delegation_result",
                "parent_agent": "general_orchestrator",
                "parent_run_id": parent_run_id,
                "children": result["children"],
                "provider": response.provider,
                "model": response.model,
                "routing": decision.to_dict(),
                "state_version": route_state.get("state_version"),
            }
        except Exception as exc:
            delegation_service.finish_parent(user, parent_run_id, status="failed", error=str(exc))
            raise


general_orchestrator = GeneralOrchestratorAgent()
