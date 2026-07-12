from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from agents.factory import ModelResponse
from agents.intent_planner import IntentPlanner, extract_research_topic


TOOLS = [
    {"name": "search_cnki_papers", "description": "Search CNKI", "input_schema": {}},
    {"name": "download_cnki_selections", "description": "Download selected CNKI papers", "input_schema": {}},
    {"name": "search_papers", "description": "Search papers", "input_schema": {}},
    {"name": "save_to_knowledge", "description": "Save a complete paper record", "input_schema": {}},
]


class IntentPlannerTest(unittest.IsolatedAsyncioTestCase):
    def test_research_topic_excludes_execution_words(self):
        cases = {
            "给我在知网搜点云，返回一篇并存储知识库": "点云",
            "搜索三维点云的论文，只返回一篇就好": "三维点云",
            "在知网搜索数据修复论文，先展示结果，不要下载": "数据修复",
            "换成异常检测，还是下载第一篇": "异常检测",
        }
        for content, expected in cases.items():
            self.assertEqual(extract_research_topic(content), expected)

    async def test_model_pipeline_is_validated_and_topic_is_repaired(self):
        payload = {
            "action": "pipeline",
            "intent": "search_and_download",
            "subject": "在 点云 返回一篇",
            "confidence": 0.97,
            "reason": "explicit request",
            "steps": [
                {"tool_name": "search_cnki_papers", "arguments": {"query": "在 点云 返回一篇", "limit": 20}},
                {"tool_name": "download_cnki_selections", "arguments": {"indexes": [1]}},
            ],
        }
        model = AsyncMock(return_value=ModelResponse(json.dumps(payload, ensure_ascii=False), "qwen", "qwen-flash"))
        with patch("agents.intent_planner.model_factory.generate_text", model):
            plan = await IntentPlanner().plan(
                content="给我在知网搜点云，返回一篇并存储知识库",
                tools=TOOLS,
                messages=[],
                working_state={"active_source": "cnki", "last_search_query": "数据修复"},
            )
        self.assertEqual(plan["execution_mode"], "tool_pipeline")
        self.assertEqual(plan["subject"], "点云")
        self.assertEqual(plan["steps"][0]["arguments"]["query"], "点云")
        self.assertEqual(plan["steps"][1]["arguments"]["indexes"], [1])

    async def test_explicit_new_topic_overrides_historical_topic(self):
        payload = {
            "action": "tool",
            "intent": "literature_search",
            "subject": "数据修复",
            "steps": [{"tool_name": "search_cnki_papers", "arguments": {"query": "数据修复"}}],
        }
        model = AsyncMock(return_value=ModelResponse(json.dumps(payload, ensure_ascii=False), "qwen", "qwen-flash"))
        with patch("agents.intent_planner.model_factory.generate_text", model):
            plan = await IntentPlanner().plan(
                content="不要沿用刚才的主题，改搜三维点云",
                tools=TOOLS,
                messages=[{"role": "user", "content": "搜索数据修复"}],
                working_state={"active_source": "cnki", "last_search_query": "数据修复"},
            )
        self.assertEqual(plan["arguments"]["query"], "三维点云")

    async def test_missing_download_step_is_completed_for_explicit_knowledge_ingest(self):
        payload = {
            "action": "tool",
            "intent": "search_and_store",
            "subject": "点云",
            "steps": [
                {"tool_name": "search_cnki_papers", "arguments": {"query": "点云"}},
                {"tool_name": "save_to_knowledge", "arguments": {}},
            ],
        }
        model = AsyncMock(return_value=ModelResponse(json.dumps(payload, ensure_ascii=False), "qwen", "qwen-flash"))
        with patch("agents.intent_planner.model_factory.generate_text", model):
            plan = await IntentPlanner().plan(
                content="在知网搜索点云，保存第3篇到知识库",
                tools=TOOLS,
                messages=[],
                working_state={"active_source": "cnki"},
            )
        self.assertEqual(plan["execution_mode"], "tool_pipeline")
        self.assertEqual([step["tool_name"] for step in plan["steps"]], [
            "search_cnki_papers",
            "download_cnki_selections",
        ])
        self.assertEqual(plan["steps"][1]["arguments"]["indexes"], [3])

    async def test_download_negation_removes_side_effect_step(self):
        payload = {
            "action": "pipeline",
            "intent": "literature_search",
            "subject": "异常检测",
            "steps": [
                {"tool_name": "search_cnki_papers", "arguments": {"query": "异常检测"}},
                {"tool_name": "download_cnki_selections", "arguments": {"indexes": [1]}},
            ],
        }
        model = AsyncMock(return_value=ModelResponse(json.dumps(payload, ensure_ascii=False), "qwen", "qwen-flash"))
        with patch("agents.intent_planner.model_factory.generate_text", model):
            plan = await IntentPlanner().plan(
                content="在知网搜索异常检测论文，先展示结果，不要下载",
                tools=TOOLS,
                messages=[],
                working_state={},
            )
        self.assertEqual(plan["execution_mode"], "tool")
        self.assertEqual(plan["tool_name"], "search_cnki_papers")
        self.assertEqual(plan["arguments"]["query"], "异常检测")


if __name__ == "__main__":
    unittest.main()
