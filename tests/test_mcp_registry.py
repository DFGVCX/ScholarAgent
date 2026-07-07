import unittest
from unittest.mock import patch
from uuid import uuid4

from mcp_server.scholar_mcp.external_sources import ExternalSourceError, _http_get_bytes
from mcp_server.scholar_mcp.external_sources import attach_paper_pdf
from mcp_server.scholar_mcp.models import PaperRecord
from mcp_server.scholar_mcp.registry import tool_registry
from mcp_server.scholar_mcp.tools import call_tool_with_safety, search_papers


class MCPRegistryTest(unittest.IsolatedAsyncioTestCase):
    async def test_meta_tools_are_registered(self):
        names = {spec["name"] for spec in tool_registry.list_specs()}
        self.assertIn("TOOL_LIST", names)
        self.assertIn("ingest_paper", names)
        self.assertIn("delete_knowledge", names)

    async def test_high_risk_tool_requires_confirmation(self):
        result = await call_tool_with_safety(
            "delete_knowledge",
            {
                "tenant_id": "tenant_demo",
                "user_id": "user_demo",
                "paper_id": "paper:arxiv:missing",
            },
        )
        self.assertEqual(result["status"], "REQUIRE_CONFIRM")

    async def test_search_falls_back_when_arxiv_times_out(self):
        query = f"fallback retrieval {uuid4()}"
        paper = PaperRecord(
            paper_id=f"paper:openalex:{uuid4().hex}",
            tenant_id="tenant_demo",
            user_id="user_demo",
            source="openalex",
            title="Fallback Retrieval Architecture",
            authors=["ScholarAgent"],
            abstract="Real external metadata can continue after arXiv timeout.",
            metadata={"external_source": "openalex", "mock": False},
        )

        with patch(
            "mcp_server.scholar_mcp.tools.search_arxiv_papers",
            side_effect=ExternalSourceError("external source unavailable: timed out"),
        ), patch(
            "mcp_server.scholar_mcp.tools.search_openalex_papers",
            return_value=[paper],
        ), patch(
            "mcp_server.scholar_mcp.tools.search_crossref_papers",
            return_value=[],
        ):
            result = await search_papers("tenant_demo", "user_demo", query, source="all", limit=3)

        self.assertTrue(any(item["paper_id"] == paper.paper_id for item in result["items"]))
        self.assertIn("arxiv:", result["external_error"])

    def test_pdf_download_uses_browser_print_fallback(self):
        paper = PaperRecord(
            paper_id=f"paper:openalex:{uuid4().hex}",
            tenant_id="tenant_demo",
            user_id="user_demo",
            source="openalex",
            title="Printable landing page",
            metadata={},
        )

        def fake_print(_url, output_path, _timeout):
            output_path.write_bytes(b"%PDF-1.4\nprinted\n%%EOF")

        with patch(
            "mcp_server.scholar_mcp.external_sources._http_get_bytes",
            side_effect=ExternalSourceError("downloaded content is not a PDF"),
        ), patch(
            "mcp_server.scholar_mcp.external_sources._print_url_to_pdf",
            side_effect=fake_print,
        ):
            updated = attach_paper_pdf(paper, ["https://example.test/article"])

        self.assertTrue(updated.metadata["pdf_downloaded"])
        self.assertEqual(updated.metadata["pdf_capture_method"], "browser_print")
        self.assertTrue(updated.metadata["file_url"].startswith("/knowledge/files/"))

    def test_http_request_retries_direct_when_proxy_is_refused(self):
        calls = []

        def fake_read(_request, _timeout, _max_bytes, *, direct):
            calls.append(direct)
            if not direct:
                raise OSError("[WinError 10061] actively refused")
            return b"ok"

        with patch("mcp_server.scholar_mcp.external_sources._read_response", side_effect=fake_read):
            data = _http_get_bytes("https://example.test", "application/json")

        self.assertEqual(data, b"ok")
        self.assertEqual(calls, [False, True])


if __name__ == "__main__":
    unittest.main()
