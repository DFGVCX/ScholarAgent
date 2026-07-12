from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from agents.factory import model_factory
from app.schemas import UserContext
from app.services.conversation_tool_store import conversation_tool_call_store
from app.services.conversation_state_service import conversation_state_service
from mcp_server.scholar_mcp.client import ScholarMCPClient


_CONFIRM_WORDS = {"确认", "同意", "继续", "确认执行", "是的", "yes", "ok"}
_CANCEL_WORDS = {"取消", "不用了", "拒绝", "否", "no"}
_ACTION_PATTERN = re.compile(
    r"检索|搜索|搜一下|查找|查询|保存|存储|入库|删除|移除|论文|文献|知识库|"
    r"引用|机构|VPN|下载|获取全文|知网"
)
_PAPER_ID_PATTERN = re.compile(r"paper:[A-Za-z0-9_:\-.]+")
_URL_PATTERN = re.compile(r"https?://[^\s<>\]\[\"']+")
_ORDINALS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


@dataclass(frozen=True)
class ToolLoopOutcome:
    content: str
    metadata: dict[str, Any]


class ConversationToolLoop:
    def __init__(self, client: ScholarMCPClient | None = None) -> None:
        self.client = client or ScholarMCPClient()

    async def run(
        self,
        user: UserContext,
        conversation_id: str,
        content: str,
        messages: list[dict[str, Any]],
    ) -> ToolLoopOutcome | None:
        normalized = content.strip()
        working_state = conversation_state_service.observe_user_message(
            user, conversation_id, normalized
        )
        pending = conversation_tool_call_store.latest_pending(user, conversation_id)
        if pending and normalized.lower() in _CONFIRM_WORDS:
            return await self.confirm(user, conversation_id, pending["call_id"], approved=True)
        if pending and normalized.lower() in _CANCEL_WORDS:
            conversation_tool_call_store.update(
                user, conversation_id, pending["call_id"], status="cancelled"
            )
            conversation_state_service.observe_tool(
                user, conversation_id, tool_name=pending["tool_name"],
                arguments=pending.get("arguments") or {}, status="cancelled",
                call_id=pending["call_id"],
            )
            return ToolLoopOutcome(
                "已取消该操作，未修改你的知识库。",
                {"kind": "tool_cancelled", "tool_call": {**pending, "status": "cancelled"}},
            )

        tools = await self.client.list_tools()
        available = {str(tool.get("name")) for tool in tools}
        ledger_has_cnki = conversation_tool_call_store.has_succeeded(
            user, conversation_id, "search_cnki_papers"
        )

        state_has_cnki = working_state.get("active_source") == "cnki"
        combined = self._combined_cnki_plan(
            normalized, allow_implicit_cnki=ledger_has_cnki or state_has_cnki
        )
        if combined and {"search_cnki_papers", "download_cnki_selections"}.issubset(available):
            route_state = conversation_state_service.record_route(
                user, conversation_id, intent="search_and_download", target="cnki_pipeline",
                execution_mode="tool_pipeline",
                reasons=["active_source_cnki", "explicit_search", "explicit_download", "selection_index_resolved"],
                confidence=0.99,
                planned_steps=["search_cnki_papers", "download_cnki_selections", "await_user_confirmation"],
            )
            search_outcome = await self._execute(
                user,
                conversation_id,
                "search_cnki_papers",
                {
                    "query": combined["query"],
                    "limit": 20,
                    "tenant_id": user.tenant_id,
                    "user_id": user.user_id,
                },
            )
            if search_outcome.metadata.get("tool_call", {}).get("status") != "succeeded":
                return search_outcome
            search_items = (search_outcome.metadata.get("result") or {}).get("items") or []
            if not search_items:
                return search_outcome
            download_outcome = await self._execute(
                user,
                conversation_id,
                "download_cnki_selections",
                {
                    "indexes": combined["indexes"],
                    "tenant_id": user.tenant_id,
                    "user_id": user.user_id,
                },
            )
            return ToolLoopOutcome(
                "已完成知网检索。\n\n" + download_outcome.content,
                {
                    **download_outcome.metadata,
                    "pipeline": ["search_cnki_papers", "download_cnki_selections"],
                    "search_result": search_outcome.metadata.get("result") or {},
                    "routing": route_state.get("last_route"),
                    "state_version": route_state.get("state_version"),
                },
            )

        plan = self._deterministic_plan(normalized, messages, ledger_has_cnki=ledger_has_cnki)
        if plan is None and _ACTION_PATTERN.search(normalized):
            plan = await self._llm_plan(normalized, tools)
        if plan is None:
            return None

        tool_name = str(plan.get("tool_name") or "")
        if tool_name not in available:
            return ToolLoopOutcome(
                f"当前没有发现可执行的工具：{tool_name or '未指定'}。",
                {"kind": "tool_error", "error": "tool_not_discovered", "tool_name": tool_name},
            )
        arguments = dict(plan.get("arguments") or {})
        arguments["tenant_id"] = user.tenant_id
        arguments["user_id"] = user.user_id
        route_state = conversation_state_service.record_route(
            user, conversation_id,
            intent=str(plan.get("intent") or "tool_action"),
            target=tool_name,
            execution_mode="tool",
            reasons=list(plan.get("reasons") or ["deterministic_intent_match"]),
            confidence=float(plan.get("confidence") or 0.9),
            planned_steps=[tool_name],
        )
        outcome = await self._execute(user, conversation_id, tool_name, arguments)
        return ToolLoopOutcome(
            outcome.content,
            {
                **outcome.metadata,
                "routing": route_state.get("last_route"),
                "state_version": route_state.get("state_version"),
            },
        )

    async def confirm(
        self,
        user: UserContext,
        conversation_id: str,
        call_id: str,
        *,
        approved: bool,
    ) -> ToolLoopOutcome:
        call = conversation_tool_call_store.get(user, conversation_id, call_id)
        if call is None:
            return ToolLoopOutcome("找不到待确认的操作。", {"kind": "tool_error", "error": "call_not_found"})
        if call["status"] != "awaiting_confirmation":
            return ToolLoopOutcome(
                "该操作已经处理，无需重复确认。",
                {"kind": "tool_result", "tool_call": call, "result": call.get("result") or {}},
            )
        if not approved:
            updated = conversation_tool_call_store.update(
                user, conversation_id, call_id, status="cancelled"
            ) or call
            conversation_state_service.observe_tool(
                user, conversation_id, tool_name=call["tool_name"],
                arguments=call.get("arguments") or {}, status="cancelled", call_id=call_id,
            )
            return ToolLoopOutcome("已取消该操作，未修改你的知识库。", {"kind": "tool_cancelled", "tool_call": updated})

        arguments = dict(call["arguments"])
        arguments["confirmation_token"] = call_id
        conversation_tool_call_store.update(user, conversation_id, call_id, status="running")
        try:
            result = await self.client.call_tool(call["tool_name"], arguments)
        except Exception as exc:
            updated = conversation_tool_call_store.update(
                user, conversation_id, call_id, status="failed", error=str(exc)
            ) or call
            conversation_state_service.observe_tool(
                user, conversation_id, tool_name=call["tool_name"],
                arguments=arguments, status="failed", error=str(exc), call_id=call_id,
            )
            return ToolLoopOutcome(
                f"工具执行失败：{exc}",
                {"kind": "tool_error", "tool_call": updated, "error": str(exc)},
            )
        updated = conversation_tool_call_store.update(
            user, conversation_id, call_id, status="succeeded", result=result
        ) or call
        conversation_state_service.observe_tool(
            user, conversation_id, tool_name=call["tool_name"], arguments=arguments,
            status="succeeded", result=result, call_id=call_id,
        )
        return ToolLoopOutcome(
            self._result_message(call["tool_name"], result),
            {"kind": "tool_result", "tool_call": updated, "result": result},
        )

    async def _execute(
        self,
        user: UserContext,
        conversation_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolLoopOutcome:
        call = conversation_tool_call_store.create(
            user, conversation_id, tool_name, arguments, status="running"
        )
        try:
            result = await self.client.call_tool(tool_name, arguments)
        except Exception as exc:
            updated = conversation_tool_call_store.update(
                user, conversation_id, call["call_id"], status="failed", error=str(exc)
            ) or call
            conversation_state_service.observe_tool(
                user, conversation_id, tool_name=tool_name, arguments=arguments,
                status="failed", error=str(exc), call_id=call["call_id"],
            )
            return ToolLoopOutcome(
                f"工具执行失败：{exc}",
                {"kind": "tool_error", "tool_call": updated, "error": str(exc)},
            )
        if result.get("status") == "REQUIRE_CONFIRM":
            updated = conversation_tool_call_store.update(
                user, conversation_id, call["call_id"], status="awaiting_confirmation", result=result
            ) or call
            conversation_state_service.observe_tool(
                user, conversation_id, tool_name=tool_name, arguments=arguments,
                status="awaiting_confirmation", result=result, call_id=call["call_id"],
            )
            return ToolLoopOutcome(
                self._confirmation_message(tool_name, arguments),
                {
                    "kind": "tool_confirmation",
                    "tool_call": updated,
                    "result": result,
                    "actions": ["confirm", "cancel"],
                },
            )
        status = "failed" if result.get("status") in {"ERROR", "DENIED"} else "succeeded"
        updated = conversation_tool_call_store.update(
            user,
            conversation_id,
            call["call_id"],
            status=status,
            result=result,
            error=str(result.get("error") or "") if status == "failed" else "",
        ) or call
        conversation_state_service.observe_tool(
            user, conversation_id, tool_name=tool_name, arguments=arguments,
            status=status, result=result,
            error=str(result.get("error") or "") if status == "failed" else "",
            call_id=call["call_id"],
        )
        return ToolLoopOutcome(
            self._result_message(tool_name, result),
            {"kind": "tool_result", "tool_call": updated, "result": result},
        )

    def _deterministic_plan(
        self,
        content: str,
        messages: list[dict[str, Any]],
        *,
        ledger_has_cnki: bool = False,
    ) -> dict[str, Any] | None:
        lowered = content.lower()
        candidate = self._selected_candidate(content, messages)
        last_search_tool = self._last_search_tool(messages)
        url_match = _URL_PATTERN.search(content)
        source_url = url_match.group(0).rstrip("。；，,)）") if url_match else ""

        if any(phrase in content for phrase in ("机构访问状态", "机构会话状态", "学校VPN状态", "学校 VPN 状态")):
            return {"tool_name": "institution_session_status", "arguments": {}}
        if any(phrase in content for phrase in ("连接机构", "启动机构登录", "连接学校", "机构登录")):
            return {"tool_name": "start_institution_login", "arguments": {}}
        if any(phrase in content for phrase in ("登录完成", "已经登录", "已完成登录", "我登录好了")):
            return {"tool_name": "confirm_institution_browser_login", "arguments": {}}
        explicit_general_source = any(
            word in lowered for word in ("全网", "联网", "openalex", "arxiv", "crossref")
        )
        has_cnki_context = ledger_has_cnki or self._has_tool_result(messages, "search_cnki_papers")
        continue_cnki = (
            (last_search_tool == "search_cnki_papers" or has_cnki_context)
            and not explicit_general_source
            and "知识库" not in content
        )
        if ("知网" in content or continue_cnki) and self._contains_search(content):
            return {
                "tool_name": "search_cnki_papers",
                "arguments": {"query": self._clean_query(content), "limit": 20},
                "intent": "literature_search",
                "reasons": ["active_source_cnki", "explicit_search", "download_explicitly_deferred"]
                if self._download_is_negated(content)
                else ["active_source_cnki", "explicit_search"],
                "confidence": 0.99,
            }
        if "验证" in content and any(word in content for word in ("机构", "VPN", "权限", "访问")) and source_url:
            return {"tool_name": "verify_institution_access", "arguments": {"probe_url": source_url}}
        if self._contains_download(content) and source_url:
            source = "cnki" if "cnki" in lowered or "知网" in content else (
                "mit_press" if "direct.mit.edu" in lowered else "institution"
            )
            return {
                "tool_name": "download_institution_url",
                "arguments": {"source_url": source_url, "source": source, "conversation_id": ""},
            }
        if self._contains_download(content):
            cnki_indexes = self._selected_cnki_indexes(content, messages)
            if cnki_indexes:
                return {"tool_name": "download_cnki_selections", "arguments": {"indexes": cnki_indexes}}
            if candidate:
                return {"tool_name": "acquire_paper_to_knowledge", "arguments": {"paper": candidate}}
            return {"tool_name": "institution_session_status", "arguments": {}}
        if any(phrase in content for phrase in ("断开机构", "退出机构", "撤销机构会话")):
            return {"tool_name": "revoke_institution_session", "arguments": {}}
        if any(word in content for word in ("保存", "存储", "入库")):
            if candidate:
                return {"tool_name": "acquire_paper_to_knowledge", "arguments": {"paper": candidate}}
            return {
                "tool_name": "search_papers",
                "arguments": {"query": self._clean_query(content), "source": "all", "limit": 5, "persist_results": False},
            }
        if any(word in content for word in ("删除", "移除")):
            paper_id_match = _PAPER_ID_PATTERN.search(content)
            paper_id = paper_id_match.group(0) if paper_id_match else str((candidate or {}).get("paper_id") or "")
            if paper_id:
                return {"tool_name": "delete_knowledge", "arguments": {"paper_id": paper_id}}
        if self._contains_search(content) and any(word in content for word in ("论文", "文献", "知识库")):
            source = "local" if "知识库" in content and "联网" not in content else "all"
            return {
                "tool_name": "search_papers",
                "arguments": {"query": self._clean_query(content), "source": source, "limit": 5, "persist_results": False},
            }
        if self._contains_search(content) and last_search_tool == "search_papers":
            return {
                "tool_name": "search_papers",
                "arguments": {"query": self._clean_query(content), "source": "all", "limit": 5, "persist_results": False},
            }
        if "知识库" in content and any(word in content for word in ("有什么", "有哪些", "看看")):
            return {"tool_name": "search_papers", "arguments": {"query": "", "source": "local", "limit": 10, "persist_results": False}}
        if lowered.startswith("paper:"):
            return {"tool_name": "search_papers", "arguments": {"query": content, "source": "local", "limit": 5}}
        return None

    def _combined_cnki_plan(
        self, content: str, *, allow_implicit_cnki: bool = False
    ) -> dict[str, Any] | None:
        explicit_general_source = any(
            word in content.lower() for word in ("全网", "联网", "openalex", "arxiv", "crossref")
        )
        uses_cnki = "知网" in content or (allow_implicit_cnki and not explicit_general_source)
        if not uses_cnki or not self._contains_search(content) or not self._contains_download(content):
            return None
        indexes = self._extract_indexes(content) or [1]
        return {"query": self._clean_query(content), "indexes": indexes}

    async def _llm_plan(self, content: str, tools: list[dict[str, Any]]) -> dict[str, Any] | None:
        allowed = {
            "search_papers", "search_cnki_papers", "institution_session_status",
            "start_institution_login", "confirm_institution_browser_login",
        }
        safe_tools = [
            {"name": item.get("name"), "description": item.get("description")}
            for item in tools if item.get("name") in allowed
        ]
        prompt = (
            "你是工具调用规划器。仅当用户明确要求执行操作时选择工具。"
            "只输出 JSON：{\"action\":\"tool|none\",\"tool_name\":\"\",\"arguments\":{}}。"
            "禁止构造论文信息，禁止选择保存、下载或删除等有副作用工具。\n"
            f"可用工具：{json.dumps(safe_tools, ensure_ascii=False)}\n用户请求：{content}"
        )
        try:
            response = await model_factory.generate_text("tool_planning", prompt, {})
            payload = self._extract_json(response.content)
        except Exception:
            return None
        if payload.get("action") != "tool":
            return None
        return {"tool_name": payload.get("tool_name"), "arguments": payload.get("arguments") or {}}

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _selected_candidate(self, content: str, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        index = self._ordinal_index(content)
        for message in reversed(messages):
            metadata = message.get("metadata") or {}
            if metadata.get("kind") != "tool_result":
                continue
            tool_call = metadata.get("tool_call") or {}
            if tool_call.get("tool_name") != "search_papers":
                continue
            items = (metadata.get("result") or {}).get("items") or []
            if items and 0 <= index < len(items):
                return dict(items[index])
        return None

    @staticmethod
    def _last_search_tool(messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            metadata = message.get("metadata") or {}
            tool_name = str((metadata.get("tool_call") or {}).get("tool_name") or "")
            if metadata.get("kind") == "tool_result" and tool_name in {"search_cnki_papers", "search_papers"}:
                return tool_name
        return ""

    @staticmethod
    def _has_tool_result(messages: list[dict[str, Any]], tool_name: str) -> bool:
        return any(
            (message.get("metadata") or {}).get("kind") == "tool_result"
            and str(((message.get("metadata") or {}).get("tool_call") or {}).get("tool_name") or "") == tool_name
            for message in messages
        )

    def _selected_cnki_indexes(self, content: str, messages: list[dict[str, Any]]) -> list[int]:
        has_cnki_results = any(
            (message.get("metadata") or {}).get("kind") == "tool_result"
            and ((message.get("metadata") or {}).get("tool_call") or {}).get("tool_name") == "search_cnki_papers"
            for message in messages
        )
        return self._extract_indexes(content) if has_cnki_results else []

    @classmethod
    def _extract_indexes(cls, content: str) -> list[int]:
        indexes = [int(value) for value in re.findall(r"\d+", content)]
        for char, value in _ORDINALS.items():
            if re.search(rf"第\s*{char}\s*(?:篇|个|条)", content):
                indexes.append(value)
        return sorted({value for value in indexes if 1 <= value <= 50})[:5]

    @classmethod
    def _ordinal_index(cls, content: str) -> int:
        digit = re.search(r"第\s*(\d+)\s*篇", content)
        if digit:
            return max(0, int(digit.group(1)) - 1)
        chinese = re.search(r"第\s*([一二三四五六七八九十])\s*(?:篇|个|条)", content)
        return _ORDINALS[chinese.group(1)] - 1 if chinese else 0

    @staticmethod
    def _contains_search(content: str) -> bool:
        return any(word in content for word in ("检索", "搜索", "搜一下", "搜", "查找", "查询"))

    @classmethod
    def _contains_download(cls, content: str) -> bool:
        if cls._download_is_negated(content):
            return False
        return any(word in content for word in ("下载", "获取全文", "拿到全文"))

    @staticmethod
    def _download_is_negated(content: str) -> bool:
        return bool(re.search(
            r"(?:不要|不用|无需|不需要|先不|暂不|别|禁止)\s*(?:下载|获取全文|拿到全文)",
            content,
        ))

    @staticmethod
    def _clean_query(content: str) -> str:
        query = re.sub(
            r"我要重新|重新|再一次|再|存入知识库|放入知识库|保存到知识库|加入知识库|请|帮我|给我|去|知网中|知网上|知网|关于|相关的|相关|论文|文献|知识库|"
            r"检索|搜索|搜一下|搜|查找|查询|下载|获取全文|拿到全文|保存|存储|入库|"
            r"第\s*[一二三四五六七八九十\d]+\s*(?:篇|个|条)|并且|然后|并",
            " ",
            content,
        )
        query = re.sub(
            r"(?:^|\s)(?:在|从|去|请|帮我|给我|找|查|返回|展示|列出|推荐|获取)\s*",
            " ",
            query,
        )
        query = re.sub(
            r"(?:返回|展示|列出|推荐|获取)?\s*(?:第?[一二三四五六七八九十\d]+|几|若干)\s*(?:篇|条|个)?(?:结果|论文|文献)?",
            " ",
            query,
        )
        return re.sub(r"\s+", " ", query).strip(" ，。；;、") or content.strip()

    @staticmethod
    def _confirmation_message(tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name == "delete_knowledge":
            return f"即将从个人知识库删除 `{arguments.get('paper_id', '')}`。该操作需要你的确认。"
        if tool_name == "download_institution_url":
            return (
                "即将使用当前机构访问权限下载并保存这篇文献到个人知识库。"
                f"\n\n来源地址：{arguments.get('source_url', '')}\n\n确认后才会发起下载。"
            )
        if tool_name == "download_cnki_selections":
            indexes = arguments.get("indexes") or []
            return (
                f"即将通过当前已登录的知网浏览器下载第 {', '.join(map(str, indexes))} 篇。"
                "下载完成后会校验 PDF/CAJ、解析正文并自动写入个人知识库。请确认执行。"
            )
        if tool_name == "revoke_institution_session":
            return "即将断开当前机构访问会话。确认后临时认证状态将失效。"
        return f"工具 `{tool_name}` 需要确认后才能继续执行。"

    @staticmethod
    def _result_message(tool_name: str, result: dict[str, Any]) -> str:
        if result.get("status") in {"ERROR", "DENIED"}:
            return f"工具执行失败：{result.get('error') or result.get('safety', {}).get('reason') or '未知错误'}"
        if tool_name == "search_papers":
            items = result.get("items") or []
            if not items:
                return "没有检索到符合条件的论文。可以补充关键词、年份或研究方向后继续检索。"
            lines = [f"已检索到 {len(items)} 篇候选论文："]
            for index, item in enumerate(items[:5], 1):
                suffix = f" · DOI: {item.get('doi')}" if item.get("doi") else ""
                lines.append(f"{index}. **{item.get('title') or '未命名论文'}** · {item.get('source') or 'unknown'}{suffix}")
            lines.append("可以继续说“下载第一篇并保存到知识库”。")
            return "\n\n".join(lines)
        if tool_name in {"save_to_knowledge", "acquire_paper_to_knowledge"}:
            paper = result.get("paper") or {}
            return (
                f"全文已下载并保存到个人知识库：**{paper.get('title') or '未命名论文'}**。"
                f"\n\n来源：{paper.get('source') or '-'}；DOI：{paper.get('doi') or '暂无'}。"
            )
        if tool_name == "delete_knowledge":
            return "论文已从个人知识库删除。"
        if tool_name == "institution_session_status":
            session = result.get("session") or {}
            status = session.get("status") or "disconnected"
            if status != "active":
                return "当前没有可用的机构浏览器会话。请先连接机构并在弹出的浏览器中完成登录，再进行知网下载。"
            return "当前机构浏览器会话可用。请先搜索论文，或指定已显示结果中的第几篇进行下载。"
        if tool_name == "start_institution_login":
            session = result.get("session") or {}
            return (
                f"机构登录会话已创建：**{session.get('institution_name') or '已配置机构'}**。"
                f"\n\n请在已打开的浏览器中完成认证：{session.get('login_url') or '-'}"
                "\n\n完成后在对话中回复“登录完成”。"
            )
        if tool_name == "confirm_institution_browser_login":
            browser = result.get("browser") or {}
            return f"机构浏览器登录状态已确认。\n\n当前页面：{browser.get('title') or '-'}\n\n现在可以直接搜索知网论文。"
        if tool_name == "search_cnki_papers":
            items = result.get("items") or []
            if not items:
                return "当前知网页面没有提取到检索结果，请检查登录状态、验证码或检索页面。"
            lines = [f"已从当前机构浏览器检索到 {len(items)} 篇知网候选论文："]
            lines.extend(
                f"{index}. **{item.get('title') or '未命名论文'}** {item.get('year') or ''}"
                for index, item in enumerate(items[:20], 1)
            )
            lines.append("选择后可以说“下载第 1、3 篇”，确认后会下载并自动入库。")
            return "\n\n".join(lines)
        if tool_name == "verify_institution_access":
            session = result.get("session") or {}
            return f"机构访问验证完成，当前状态：**{session.get('status') or 'unknown'}**。"
        if tool_name in {"download_institution_url", "download_institution_paper"}:
            paper = result.get("paper") or {}
            download = result.get("download") or {}
            return (
                f"机构文献已下载并保存到个人知识库：**{paper.get('title') or '机构文献'}**。"
                f"\n\n格式：{download.get('file_type') or '-'}；大小：{download.get('file_size') or 0} 字节。"
            )
        if tool_name == "download_cnki_selections":
            papers = result.get("items") or []
            if not papers:
                return "知网下载没有产生可入库文件，请检查登录权限、验证码或论文下载权限。"
            lines = [f"已下载、解析并入库 {len(papers)} 篇知网文献："]
            lines.extend(f"- **{paper.get('title') or '未命名论文'}**" for paper in papers)
            return "\n".join(lines)
        if tool_name == "revoke_institution_session":
            return "机构访问会话已断开。"
        return f"工具 `{tool_name}` 已执行完成。"


conversation_tool_loop = ConversationToolLoop()
