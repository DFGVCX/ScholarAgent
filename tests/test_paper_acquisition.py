from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.config import get_settings
from mcp_server.scholar_mcp.models import PaperRecord
from mcp_server.scholar_mcp.client import ScholarMCPClient
from mcp_server.scholar_mcp.tools import (
    _prepare_external_search_results,
    acquire_paper_to_knowledge,
)


class PaperAcquisitionTest(unittest.IsolatedAsyncioTestCase):
    async def test_standard_mcp_error_is_not_reported_as_success(self):
        class Result:
            isError = True
            structuredContent = None
            content = [type("TextBlock", (), {"text": "download failed"})()]

        class Session:
            async def call_tool(self, name, arguments):
                return Result()

        class Context:
            async def __aenter__(self):
                return Session()

            async def __aexit__(self, exc_type, exc, traceback):
                return None

        client = ScholarMCPClient("http://127.0.0.1:8001/mcp/")
        with patch("mcp_server.scholar_mcp.client._MCPHttpSession", return_value=Context()):
            result = await client.call_tool("acquire_paper_to_knowledge", {})
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"], "download failed")

    async def test_topic_search_does_not_download_candidates(self):
        marker = uuid4().hex
        paper = PaperRecord(
            paper_id=f"paper:openalex:{marker}",
            tenant_id=f"tenant_{marker}",
            user_id=f"user_{marker}",
            source="openalex",
            title="Metadata only candidate",
            metadata={"pdf_url": "https://example.org/paper.pdf", "is_oa": True},
        )
        with patch("mcp_server.scholar_mcp.tools.attach_paper_pdf") as downloader:
            result = await _prepare_external_search_results([paper], persist_results=False)
        downloader.assert_not_called()
        self.assertTrue(result[0]["metadata"]["full_text_available"])
        self.assertFalse(result[0]["file_path"])

    async def test_selected_candidate_downloads_before_knowledge_save(self):
        marker = uuid4().hex
        directory = get_settings().storage_dir / "test-artifacts"
        directory.mkdir(parents=True, exist_ok=True)
        file_path = Path(directory) / f"selected-{marker}.pdf"
        file_path.write_bytes(b"%PDF-1.4\n%%EOF")
        candidate = {
            "paper_id": f"paper:openalex:{marker}",
            "source": "openalex",
            "title": "Selected candidate",
            "metadata": {"pdf_url": "https://example.org/selected.pdf"},
        }

        def attach(record, urls):
            record.file_path = str(file_path)
            record.metadata["file_path"] = str(file_path)
            record.metadata["file_url"] = f"/knowledge/files/{record.paper_id}"
            return record

        try:
            with patch("mcp_server.scholar_mcp.tools.attach_paper_pdf", side_effect=attach) as downloader:
                result = await acquire_paper_to_knowledge(
                    tenant_id=f"tenant_{marker}",
                    user_id=f"user_{marker}",
                    paper=candidate,
                )
        finally:
            file_path.unlink(missing_ok=True)
        self.assertTrue(result["acquired"])
        self.assertEqual(result["paper"]["title"], "Selected candidate")
        downloader.assert_called_once()


if __name__ == "__main__":
    unittest.main()
