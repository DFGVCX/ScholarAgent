from __future__ import annotations

import unittest
from unittest.mock import patch

from agents.context.manager import ConversationContextManager
from app.schemas import UserContext


class ConversationContextManagerTest(unittest.TestCase):
    def setUp(self):
        self.user = UserContext(
            tenant_id="tenant_context", user_id="user_context",
        )
        self.manager = ConversationContextManager(max_tokens=1200, tail_messages=4)

    def test_recall_uses_real_tool_results(self):
        messages = [
            {"message_id": "m1", "role": "user", "content": "搜索知网异常检测", "metadata": {}},
            {
                "message_id": "m2", "role": "assistant", "content": "找到论文",
                "metadata": {
                    "kind": "tool_result",
                    "tool_call": {"call_id": "c1", "tool_name": "search_cnki_papers", "status": "succeeded"},
                    "result": {"items": [{"title": "异常检测研究", "source": "cnki"}]},
                },
            },
            {
                "message_id": "m3", "role": "assistant", "content": "已入库",
                "metadata": {
                    "kind": "tool_result",
                    "tool_call": {"call_id": "c2", "tool_name": "download_cnki_selections", "status": "succeeded"},
                    "result": {"items": [{"paper_id": "paper:cnki:1", "title": "异常检测研究"}]},
                },
            },
        ]
        with patch.object(self.manager, "_load", return_value={}), patch.object(self.manager, "_save"):
            bundle = self.manager.build(self.user, "conversation_1", messages)
        answer = self.manager.recall(bundle.events)
        self.assertIn("在知网检索论文", answer)
        self.assertIn("下载选中的知网论文并写入知识库", answer)
        self.assertIn("异常检测研究", answer)
        self.assertIn("真实状态和结果", bundle.prompt)

    def test_middle_messages_are_compacted_but_head_and_tail_remain(self):
        messages = [
            {"message_id": f"m{i}", "role": "user" if i % 2 == 0 else "assistant", "content": f"message-{i}", "metadata": {}}
            for i in range(14)
        ]
        with patch.object(self.manager, "_load", return_value={}), patch.object(self.manager, "_save"):
            bundle = self.manager.build(self.user, "conversation_2", messages)
        self.assertTrue(bundle.compressed)
        self.assertIn("message-0", bundle.prompt)
        self.assertIn("message-13", bundle.prompt)
        self.assertIn("message-5", bundle.summary)

    def test_context_honors_hard_token_budget(self):
        manager = ConversationContextManager(max_tokens=260, tail_messages=6)
        messages = [
            {"message_id": f"budget-{index}", "role": "user" if index % 2 == 0 else "assistant",
             "content": (f"第 {index} 条消息 " + "很长的上下文 " * 120), "metadata": {}}
            for index in range(18)
        ]
        with patch.object(manager, "_load", return_value={}), \
             patch.object(manager, "_save"), \
             patch("agents.context.manager.user_memory_service.extract_from_messages", return_value=[]), \
             patch("agents.context.manager.user_memory_service.recall", return_value=[]):
            bundle = manager.build(self.user, "conversation_budget", messages)
        self.assertLessEqual(bundle.estimated_tokens, 260)
        self.assertIn("第 16 条消息", bundle.prompt)

    def test_context_storage_keys_include_tenant_user_and_conversation(self):
        with patch("agents.context.manager.mysql_store.fetch_one", return_value=None) as fetch:
            self.manager._load(self.user, "conversation_3")
        self.assertEqual(fetch.call_args.args[1], ("tenant_context", "user_context", "conversation_3"))

    def test_latest_state_replaces_confirmation_for_same_tool_call(self):
        messages = [
            {"role": "assistant", "content": "请确认", "metadata": {"kind": "tool_confirmation", "tool_call": {"call_id": "same", "tool_name": "download_cnki_selections", "status": "awaiting_confirmation"}}},
            {"role": "assistant", "content": "已完成", "metadata": {"kind": "tool_result", "tool_call": {"call_id": "same", "tool_name": "download_cnki_selections", "status": "succeeded"}, "result": {"items": [{"title": "真实论文"}]}}},
        ]
        events = self.manager._tool_events(messages)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "succeeded")
        self.assertEqual(events[0]["result"]["items"][0]["title"], "真实论文")

    def test_reader_event_is_tenant_scoped(self):
        with patch("agents.context.manager.mysql_store.execute") as execute:
            event = self.manager.record_event(
                self.user, "conversation_4", "inspect_reader", "已检查论文阅读效果",
                {"paper_id": "paper:cnki:1", "page_count": 14},
            )
        self.assertEqual(event["event_type"], "inspect_reader")
        params = execute.call_args.args[1]
        self.assertEqual(params[2:5], ("tenant_context", "user_context", "inspect_reader"))


if __name__ == "__main__":
    unittest.main()
