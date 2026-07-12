from __future__ import annotations

import unittest
from uuid import uuid4

from agents.conversation_tool_loop import conversation_tool_loop
from app.routes.conversations import (
    ConversationCreateDTO,
    ConversationMessageDTO,
    ToolConfirmationDTO,
    append_message,
    archive_conversation,
    confirm_tool_call,
    create_conversation,
)


class _FakeMCPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self):
        return [
            {"name": "search_papers"}, {"name": "save_to_knowledge"},
            {"name": "acquire_paper_to_knowledge"}, {"name": "delete_knowledge"},
            {"name": "search_cnki_papers"}, {"name": "download_cnki_selections"},
            {"name": "institution_session_status"},
        ]

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if name == "search_papers":
            return {"status": "OK", "items": [{
                "paper_id": "paper:tool-loop-1",
                "title": "Reliable Tool-Using Research Agents",
                "authors": "A. Researcher", "year": 2026,
                "doi": "10.1000/tool-loop", "source": "openalex",
            }]}
        if name in {"save_to_knowledge", "acquire_paper_to_knowledge"}:
            return {"status": "OK", "paper": arguments["paper"], "acquired": True}
        if name == "delete_knowledge" and not arguments.get("confirmation_token"):
            return {"status": "REQUIRE_CONFIRM", "safety": {"level": "HIGH"}}
        if name == "delete_knowledge":
            return {"status": "OK", "deleted": True, "paper_id": arguments["paper_id"]}
        if name == "search_cnki_papers":
            return {"status": "OK", "items": [
                {"title": f"知网结果 {index}", "detail_url": f"https://kns.cnki.net/{index}", "source": "cnki"}
                for index in range(1, 5)
            ]}
        if name == "download_cnki_selections" and not arguments.get("confirmation_token"):
            return {"status": "REQUIRE_CONFIRM", "safety": {"level": "HIGH"}}
        if name == "download_cnki_selections":
            return {"status": "OK", "items": [
                {"title": f"知网结果 {index}", "source": "cnki"} for index in arguments["indexes"]
            ]}
        if name == "institution_session_status":
            return {"status": "OK", "session": {"status": "active"}}
        return {"status": "ERROR", "error": "unexpected tool"}


class _NoopIntentPlanner:
    async def plan(self, **kwargs):
        return None


class ConversationToolLoopTest(unittest.IsolatedAsyncioTestCase):
    def test_cnki_query_removes_command_words_and_requested_count(self):
        self.assertEqual(
            conversation_tool_loop._clean_query("在知网搜索点云论文，返回一篇"),
            "点云",
        )

    async def asyncSetUp(self):
        self.previous_client = conversation_tool_loop.client
        self.previous_planner = conversation_tool_loop.planner
        self.client = _FakeMCPClient()
        conversation_tool_loop.client = self.client
        conversation_tool_loop.planner = _NoopIntentPlanner()
        self.conversation_id = None

    async def asyncTearDown(self):
        conversation_tool_loop.client = self.previous_client
        conversation_tool_loop.planner = self.previous_planner
        if self.conversation_id:
            await archive_conversation(self.conversation_id, x_api_key="demo-key")

    async def _create(self, initial_message: str):
        created = await create_conversation(
            ConversationCreateDTO(
                title=f"Tool loop {uuid4()}", skill_id="general_assistant",
                initial_message=initial_message,
            ),
            x_api_key="demo-key",
        )
        self.conversation_id = created["item"]["conversation_id"]
        return created

    async def test_search_download_and_confirmed_delete_with_normal_chinese(self):
        created = await self._create("帮我搜索研究智能体相关论文")
        search_reply = created["item"]["messages"][1]
        self.assertEqual(search_reply["metadata"]["tool_call"]["tool_name"], "search_papers")

        downloaded = await append_message(
            self.conversation_id,
            ConversationMessageDTO(content="下载第一篇并保存到知识库"),
            x_api_key="demo-key",
        )
        self.assertEqual(
            downloaded["items"][1]["metadata"]["tool_call"]["tool_name"],
            "acquire_paper_to_knowledge",
        )
        self.assertEqual(self.client.calls[-1][1]["paper"]["paper_id"], "paper:tool-loop-1")

        deletion = await append_message(
            self.conversation_id,
            ConversationMessageDTO(content="删除 paper:tool-loop-1"),
            x_api_key="demo-key",
        )
        confirmation = deletion["items"][1]
        self.assertEqual(confirmation["metadata"]["kind"], "tool_confirmation")
        confirmed = await confirm_tool_call(
            self.conversation_id,
            confirmation["metadata"]["tool_call"]["call_id"],
            ToolConfirmationDTO(approved=True),
            x_api_key="demo-key",
        )
        self.assertEqual(confirmed["item"]["metadata"]["kind"], "tool_result")

    async def test_cnki_search_then_download_selection(self):
        created = await self._create("搜索知网中关于多智能体科研写作的论文")
        self.assertEqual(
            created["item"]["messages"][1]["metadata"]["tool_call"]["tool_name"],
            "search_cnki_papers",
        )
        selected = await append_message(
            self.conversation_id,
            ConversationMessageDTO(content="下载第 1、3 篇"),
            x_api_key="demo-key",
        )
        confirmation = selected["items"][1]
        self.assertEqual(confirmation["metadata"]["kind"], "tool_confirmation")
        self.assertEqual(confirmation["metadata"]["tool_call"]["arguments"]["indexes"], [1, 3])

    async def test_follow_up_search_keeps_cnki_source_from_conversation(self):
        await self._create("在知网搜索异常检测论文")
        searched = await append_message(
            self.conversation_id,
            ConversationMessageDTO(content="我要重新搜索数据修复"),
            x_api_key="demo-key",
        )
        reply = searched["items"][1]
        self.assertEqual(reply["metadata"]["tool_call"]["tool_name"], "search_cnki_papers")
        self.assertEqual(reply["metadata"]["tool_call"]["arguments"]["query"], "数据修复")

    def test_cnki_context_survives_one_accidental_generic_search(self):
        messages = [
            {"metadata": {"kind": "tool_result", "tool_call": {"tool_name": "search_cnki_papers"}}},
            {"metadata": {"kind": "tool_result", "tool_call": {"tool_name": "search_papers"}}},
        ]
        plan = conversation_tool_loop._deterministic_plan(
            "我要重新搜索数据修复", messages, ledger_has_cnki=True
        )
        self.assertEqual(plan["tool_name"], "search_cnki_papers")
        self.assertEqual(plan["arguments"]["query"], "数据修复")

    def test_cnki_context_supports_search_and_download_in_one_follow_up(self):
        plan = conversation_tool_loop._combined_cnki_plan(
            "搜索数据修复，下载第一个", allow_implicit_cnki=True
        )
        self.assertEqual(plan, {"query": "数据修复", "indexes": [1]})

    def test_cnki_search_respects_explicit_download_negation(self):
        self.assertIsNone(conversation_tool_loop._combined_cnki_plan(
            "在知网搜索数据修复论文，先展示结果，不要下载",
            allow_implicit_cnki=True,
        ))
        plan = conversation_tool_loop._deterministic_plan(
            "在知网搜索数据修复论文，先展示结果，不要下载", [], ledger_has_cnki=False
        )
        self.assertEqual(plan["tool_name"], "search_cnki_papers")
        self.assertIn("download_explicitly_deferred", plan["reasons"])

    async def test_cnki_search_and_download_can_be_requested_in_one_turn(self):
        created = await self._create("去知网搜索异常检测，下载第一篇并存入知识库")
        reply = created["item"]["messages"][1]
        self.assertEqual(reply["metadata"]["kind"], "tool_confirmation")
        self.assertEqual(reply["metadata"]["pipeline"], ["search_cnki_papers", "download_cnki_selections"])
        self.assertEqual([name for name, _ in self.client.calls[-2:]], ["search_cnki_papers", "download_cnki_selections"])
        self.assertEqual(reply["metadata"]["tool_call"]["arguments"]["indexes"], [1])
        self.assertEqual(self.client.calls[-2][1]["query"], "异常检测")


if __name__ == "__main__":
    unittest.main()
