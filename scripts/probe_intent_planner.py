from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.intent_planner import intent_planner
from mcp_server.scholar_mcp.client import ScholarMCPClient


async def probe(message: str, previous_topic: str, source: str) -> tuple[dict | None, list[str]]:
    tools = await ScholarMCPClient().list_tools()
    result = await intent_planner.plan(
        content=message,
        tools=tools,
        messages=(
            [{"role": "user", "content": f"之前检索的是{previous_topic}"}]
            if previous_topic
            else []
        ),
        working_state={
            "active_domain": "literature" if source else "",
            "active_source": source,
            "last_search_query": previous_topic,
            "previous_goal": f"搜索{previous_topic}" if previous_topic else "",
        },
    )
    return result, [str(item.get("name") or "") for item in tools]


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect intent planning without executing tools")
    parser.add_argument("--message", default="")
    parser.add_argument("--message-b64", default="")
    parser.add_argument("--previous-topic", default="")
    parser.add_argument("--source", default="")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--ascii", action="store_true")
    args = parser.parse_args()
    message = args.message
    if args.message_b64:
        message = base64.b64decode(args.message_b64).decode("utf-8")
    if not message.strip():
        parser.error("--message or --message-b64 is required")
    result, tools = asyncio.run(probe(message, args.previous_topic, args.source))
    output = {"message": message, "available_tools": tools, "plan": result} if args.debug else result
    print(json.dumps(output, ensure_ascii=args.ascii, indent=2))
    return 0 if result else 2


if __name__ == "__main__":
    raise SystemExit(main())
