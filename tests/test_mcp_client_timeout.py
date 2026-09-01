from __future__ import annotations

import unittest

from mcp_server.scholar_mcp.client import ScholarMCPClient, _MCPHttpSession


class McpClientTimeoutTest(unittest.TestCase):
    def test_long_running_ingestion_can_override_http_timeout(self) -> None:
        client = ScholarMCPClient(
            "http://127.0.0.1:8001/mcp/",
            timeout_seconds=300.0,
        )
        session = _MCPHttpSession(client.url, client.token, client.timeout_seconds)

        self.assertEqual(client.timeout_seconds, 300.0)
        self.assertEqual(session.timeout_seconds, 300.0)


if __name__ == "__main__":
    unittest.main()
