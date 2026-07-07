from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from mcp_server.scholar_mcp.client import ScholarMCPClient


async def demo() -> None:
    client = ScholarMCPClient()
    tools = await client.call_tool("TOOL_LIST", {})
    print(json.dumps(tools, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(demo())
