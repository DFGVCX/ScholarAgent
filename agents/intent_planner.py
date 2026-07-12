from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from agents.factory import model_factory


_PAPER_ID_PATTERN = re.compile(r"paper:[A-Za-z0-9_:\-.]+")
_ACTION_BOUNDARY = re.compile(
    r"(?:只(?:要|返回|展示)?|返回|展示|列出|推荐|并且|然后|并|下载|保存|存储|入库|加入知识库|放入知识库|不要下载|无需下载)"
)
_TOPIC_PATTERNS = (
    re.compile(
        r"(?:在|从|去)?\s*(?:知网|CNKI|全网|联网|OpenAlex|arXiv|Crossref)?\s*"
        r"(?:中|上)?\s*(?:搜索|搜一下|搜|检索|查找|查询|查)\s*(.+)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:换成|改成|主题(?:改为|换成|是)?|研究|关于)\s*(.+)", re.IGNORECASE),
)


def normalize_research_topic(candidate: str, original: str = "") -> str:
    """Keep the semantic research subject and remove execution instructions."""
    text = (candidate or "").strip()
    if not text or text == original.strip():
        text = extract_research_topic(original)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(
        r"^(?:请|麻烦|帮我|给我|我要|我想|去|在|从|重新|再|再一次)\s*",
        "",
        text,
    )
    boundary = _ACTION_BOUNDARY.search(text)
    if boundary:
        text = text[: boundary.start()]
    text = re.split(r"[，,。；;]\s*(?:先|还是|就|再|然后)?", text, maxsplit=1)[0]
    text = re.sub(r"(?:相关的?|方面的?)?\s*(?:论文|文献|文章)\s*$", "", text)
    text = re.sub(r"[，,。；;：:]\s*$", "", text)
    return re.sub(r"\s+", " ", text).strip(" 的，,。；;：:")


def extract_research_topic(content: str) -> str:
    text = (content or "").strip()
    for pattern in _TOPIC_PATTERNS:
        match = pattern.search(text)
        if match:
            return normalize_research_topic(match.group(1))
    boundary = _ACTION_BOUNDARY.search(text)
    if boundary:
        text = text[: boundary.start()]
    return normalize_research_topic(text)


class IntentPlanner:
    """Context-aware planner inspired by a model-first agent loop."""

    def __init__(self, timeout_seconds: float = 18.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def plan(
        self,
        *,
        content: str,
        tools: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        working_state: dict[str, Any],
        scope: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        available = {str(item.get("name") or "") for item in tools}
        tool_specs = [
            {
                "name": item.get("name"),
                "description": item.get("description") or "",
                "arguments": sorted((item.get("input_schema") or {}).get("properties", {}).keys()),
                "required": (item.get("input_schema") or {}).get("required") or [],
            }
            for item in tools
            if item.get("name")
        ]
        prompt = self._prompt(content, tool_specs, messages, working_state)
        try:
            response = await asyncio.wait_for(
                model_factory.generate_text(
                    "intent_planning",
                    prompt,
                    {
                        **(scope or {}),
                        "conversation_state": self._safe_state(working_state),
                        "structured_output": True,
                    },
                ),
                timeout=self.timeout_seconds,
            )
            payload = self._extract_json(response.content)
        except Exception:
            return None
        return self._validate(payload, available, content, working_state)

    @staticmethod
    def _prompt(
        content: str,
        tools: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        state: dict[str, Any],
    ) -> str:
        recent = []
        for message in messages[-6:]:
            metadata = message.get("metadata") or {}
            tool_call = metadata.get("tool_call") or {}
            entry = {
                "role": message.get("role"),
                "content": str(message.get("content") or "")[:320],
            }
            if tool_call.get("tool_name"):
                entry["tool"] = {
                    "name": tool_call.get("tool_name"),
                    "status": tool_call.get("status"),
                    "arguments": tool_call.get("arguments") or {},
                }
            recent.append(entry)
        contract = {
            "action": "tool | pipeline | none | clarify",
            "intent": "short intent name",
            "subject": "semantic research subject only",
            "confidence": "0..1",
            "reason": "short reason",
            "steps": [
                {"tool_name": "registered tool name", "arguments": {}}
            ],
        }
        return (
            "你是 ScholarAgent 的主控调度 Agent。像成熟 Tool Loop 一样，先理解用户真实目标，再决定工具。\n"
            "必须区分研究主题、数据源、动作、数量、选择序号和否定约束。subject 只能包含研究主题，"
            "不能包含‘给我、知网、搜索、返回一篇、下载、保存、知识库’等操作词。\n"
            "示例：‘给我在知网搜点云，返回一篇并存入知识库’的 subject 是‘点云’，步骤是"
            " search_cnki_papers(query=点云) 后 download_cnki_selections(indexes=[1])。\n"
            "显式的新主题永远覆盖历史主题；省略来源时才继承 active_source；‘不要下载’不得规划下载。\n"
            "下载、删除等副作用动作可以规划，但系统会单独要求用户确认。不要虚构工具或论文。\n"
            "如果只是普通聊天，action=none。只输出一个 JSON 对象，不要 Markdown。\n"
            f"输出契约：{json.dumps(contract, ensure_ascii=False)}\n"
            f"当前状态：{json.dumps(IntentPlanner._safe_state(state), ensure_ascii=False)}\n"
            f"最近会话：{json.dumps(recent, ensure_ascii=False)}\n"
            f"可用工具：{json.dumps(tools, ensure_ascii=False)}\n"
            f"当前用户消息：{content}"
        )

    @staticmethod
    def _safe_state(state: dict[str, Any]) -> dict[str, Any]:
        return {
            key: state.get(key)
            for key in (
                "previous_goal",
                "current_goal",
                "active_domain",
                "active_source",
                "phase",
                "last_search_query",
                "last_successful_tool",
                "last_route",
                "recent_results",
            )
            if state.get(key) not in (None, "", [], {})
        }

    @classmethod
    def _validate(
        cls,
        payload: dict[str, Any],
        available: set[str],
        content: str,
        state: dict[str, Any],
    ) -> dict[str, Any] | None:
        action = str(payload.get("action") or "none").lower()
        if action not in {"tool", "pipeline"}:
            return None
        raw_steps = payload.get("steps") or []
        if not raw_steps and payload.get("tool_name"):
            raw_steps = [{"tool_name": payload.get("tool_name"), "arguments": payload.get("arguments") or {}}]
        if not isinstance(raw_steps, list) or not raw_steps:
            return None
        subject = normalize_research_topic(str(payload.get("subject") or ""), content)
        if re.search(
            r"(?:搜索|搜一下|搜|检索|查找|查询|换成|改成|改搜|主题(?:改为|换成|是))",
            content,
        ):
            explicit_subject = extract_research_topic(content)
            if explicit_subject:
                subject = explicit_subject
        steps: list[dict[str, Any]] = []
        for raw_step in raw_steps[:4]:
            if not isinstance(raw_step, dict):
                continue
            tool_name = str(raw_step.get("tool_name") or "")
            if tool_name not in available:
                continue
            if tool_name == "download_cnki_selections" and not any(
                marker in content for marker in ("下载", "获取全文", "拿到全文", "保存", "存储", "入库", "知识库")
            ):
                continue
            if tool_name == "download_cnki_selections" and re.search(
                r"(?:不要|不用|无需|不需要|先不|暂不)\s*(?:下载|保存|存储|入库)",
                content,
            ):
                continue
            if tool_name == "delete_knowledge" and not any(
                marker in content for marker in ("删除", "移除")
            ):
                continue
            arguments = dict(raw_step.get("arguments") or {})
            arguments.pop("tenant_id", None)
            arguments.pop("user_id", None)
            arguments.pop("confirmation_token", None)
            normalized = cls._normalize_arguments(
                tool_name, arguments, subject, content, state
            )
            if normalized is not None:
                steps.append({"tool_name": tool_name, "arguments": normalized})
        if not steps:
            return None
        step_names = [step["tool_name"] for step in steps]
        requests_knowledge_ingest = any(
            marker in content for marker in ("保存", "存储", "入库", "加入知识库", "放入知识库", "存到知识库")
        )
        download_negated = bool(
            re.search(
                r"(?:不要|不用|无需|不需要|先不|暂不)\s*(?:下载|保存|存储|入库)",
                content,
            )
        )
        if (
            step_names
            and step_names[0] == "search_cnki_papers"
            and "download_cnki_selections" in available
            and requests_knowledge_ingest
            and not download_negated
        ):
            steps = [
                steps[0],
                {
                    "tool_name": "download_cnki_selections",
                    "arguments": {"indexes": cls._selection_indexes(content)},
                },
            ]
        if len(steps) > 1 and [step["tool_name"] for step in steps] != [
            "search_cnki_papers",
            "download_cnki_selections",
        ]:
            steps = steps[:1]
        confidence = payload.get("confidence", 0.85)
        try:
            confidence = max(0.0, min(float(confidence), 1.0))
        except (TypeError, ValueError):
            confidence = 0.85
        result = {
            "intent": str(payload.get("intent") or "tool_action"),
            "subject": subject,
            "confidence": confidence,
            "reasons": ["model_context_plan", str(payload.get("reason") or "semantic_intent")],
            "execution_mode": "tool_pipeline" if len(steps) > 1 else "tool",
            "steps": steps,
        }
        if len(steps) == 1:
            result.update(steps[0])
        return result

    @staticmethod
    def _selection_indexes(content: str) -> list[int]:
        indexes = [int(value) for value in re.findall(r"第?\s*(\d+)\s*(?:篇|条|个)", content)]
        chinese = {
            "一": 1,
            "二": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        indexes.extend(
            value
            for char, value in chinese.items()
            if re.search(rf"第?\s*{char}\s*(?:篇|条|个)", content)
        )
        return sorted({value for value in indexes if 1 <= value <= 50})[:5] or [1]

    @staticmethod
    def _normalize_arguments(
        tool_name: str,
        arguments: dict[str, Any],
        subject: str,
        content: str,
        state: dict[str, Any],
    ) -> dict[str, Any] | None:
        if tool_name in {"search_cnki_papers", "search_papers"}:
            query = normalize_research_topic(
                subject or str(arguments.get("query") or ""), content
            )
            if not query:
                query = normalize_research_topic(
                    str(state.get("last_search_query") or "")
                )
            if not query:
                return None
            limit = arguments.get("limit", 20 if tool_name == "search_cnki_papers" else 5)
            try:
                limit = max(1, min(int(limit), 50))
            except (TypeError, ValueError):
                limit = 20 if tool_name == "search_cnki_papers" else 5
            normalized: dict[str, Any] = {"query": query, "limit": limit}
            if tool_name == "search_papers":
                source = str(arguments.get("source") or state.get("active_source") or "all").lower()
                normalized["source"] = source if source in {"all", "local", "openalex", "arxiv", "crossref"} else "all"
                normalized["persist_results"] = False
            return normalized
        if tool_name == "download_cnki_selections":
            indexes = arguments.get("indexes") or []
            if isinstance(indexes, (int, str)):
                indexes = [indexes]
            clean = []
            for value in indexes:
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    continue
                if 1 <= number <= 50 and number not in clean:
                    clean.append(number)
            return {"indexes": clean[:5] or [1]}
        if tool_name == "delete_knowledge":
            paper_id = str(arguments.get("paper_id") or "")
            match = _PAPER_ID_PATTERN.search(paper_id or content)
            return {"paper_id": match.group(0)} if match else None
        if tool_name in {
            "institution_session_status",
            "start_institution_login",
            "confirm_institution_browser_login",
            "revoke_institution_session",
        }:
            return {}
        return arguments

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        match = re.search(r"\{.*\}", text or "", re.DOTALL)
        if not match:
            return {}
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}


intent_planner = IntentPlanner()
