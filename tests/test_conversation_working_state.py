from __future__ import annotations

import unittest
from uuid import uuid4

from agents.orchestrator import general_orchestrator
from app.schemas import UserContext
from app.services.conversation_state_service import ConversationStateService


class ConversationWorkingStateTest(unittest.TestCase):
    def setUp(self) -> None:
        marker = uuid4().hex
        self.user = UserContext(f"tenant_{marker}", f"user_{marker}")
        self.other = UserContext(f"tenant_other_{marker}", f"user_other_{marker}")
        self.conversation_id = f"conversation_{marker}"
        self.service = ConversationStateService()

    def test_tool_events_reduce_to_versioned_recoverable_state(self):
        first = self.service.observe_user_message(
            self.user, self.conversation_id, "在知网搜索数据修复"
        )
        searched = self.service.observe_tool(
            self.user,
            self.conversation_id,
            tool_name="search_cnki_papers",
            arguments={"query": "数据修复"},
            status="succeeded",
            result={"items": [{"title": "数据修复论文", "source": "cnki"}]},
            call_id="call_search",
        )
        pending = self.service.observe_tool(
            self.user,
            self.conversation_id,
            tool_name="download_cnki_selections",
            arguments={"indexes": [1]},
            status="awaiting_confirmation",
            result={"status": "REQUIRE_CONFIRM"},
            call_id="call_download",
        )
        restored = ConversationStateService().get(self.user, self.conversation_id)

        self.assertGreater(searched["state_version"], first["state_version"])
        self.assertEqual(restored["active_source"], "cnki")
        self.assertEqual(restored["phase"], "awaiting_confirmation")
        self.assertEqual(restored["pending_action"]["arguments"]["indexes"], [1])
        self.assertEqual(pending["state_version"], restored["state_version"])

    def test_working_state_is_tenant_scoped(self):
        self.service.observe_user_message(self.user, self.conversation_id, "私有研究目标")
        other_state = self.service.get(self.other, self.conversation_id)
        self.assertEqual(other_state["current_goal"], "")
        self.assertEqual(other_state["state_version"], 0)

    def test_router_exposes_reasons_capabilities_and_steps(self):
        decision = general_orchestrator.decide(
            "请对比分析三个方案并形成完整研究综述",
            working_state={"active_source": "cnki"},
        )
        self.assertEqual(decision.target_agent, "writing_agent")
        self.assertEqual(decision.execution_mode, "delegation")
        self.assertIn("writing_intent", decision.reasons)
        self.assertIn("active_source:cnki", decision.reasons)
        self.assertIn("literature_retrieval", decision.required_capabilities)
        self.assertIn("review_output", decision.planned_steps)


if __name__ == "__main__":
    unittest.main()
