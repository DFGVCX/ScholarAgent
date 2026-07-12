from __future__ import annotations

import unittest
import shutil
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.schemas import UserContext
from app.services.institutional_access.service import (
    InstitutionalAccessError,
    _knowledge_source_url,
    _persist_pdf_only,
    _request_bytes,
    institutional_access_service,
)
from app.services.institutional_access.store import institutional_access_store
from mcp_server.scholar_mcp.store import knowledge_store


class InstitutionalAccessTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        marker = uuid4().hex
        self.user = UserContext(tenant_id=f"tenant_{marker}", user_id=f"user_{marker}")
        self.other = UserContext(tenant_id=f"other_{marker}", user_id=f"other_user_{marker}")
        with patch("app.services.institutional_access.service._validate_public_url"):
            self.profile = institutional_access_service.save_profile(
                self.user,
                institution_name="Test University",
                access_type="system_vpn",
                login_url="https://library.example.edu/login",
            )
        self.session = institutional_access_service.start_session(self.user, self.profile["profile_id"])
        institutional_access_store.update_session(
            self.user,
            self.session["session_id"],
            status="active",
            authenticated_domains=["publisher.example"],
            verified_at="2026-07-11T00:00:00+00:00",
            expires_at="2099-07-11T00:00:00+00:00",
        )

    def test_profiles_and_sessions_are_tenant_scoped(self):
        self.assertEqual(len(institutional_access_service.list_profiles(self.user)), 1)
        self.assertEqual(institutional_access_service.list_profiles(self.other), [])
        self.assertEqual(
            institutional_access_service.status(self.other, self.session["session_id"])["status"],
            "disconnected",
        )

    def test_expired_active_session_is_not_reported_as_connected(self):
        institutional_access_store.update_session(
            self.user,
            self.session["session_id"],
            status="active",
            expires_at="2020-01-01T00:00:00+00:00",
        )
        status = institutional_access_service.status(self.user, self.session["session_id"])
        self.assertEqual(status["status"], "expired")
        self.assertIn("重新连接", status["last_error"])

    def test_private_download_url_is_blocked(self):
        with self.assertRaises(InstitutionalAccessError) as context:
            institutional_access_service.prepare_download(
                self.user,
                session_id=self.session["session_id"],
                source_url="http://127.0.0.1/private.pdf",
                title="Blocked",
            )
        self.assertEqual(context.exception.code, "SOURCE_URL_BLOCKED")

    def test_proxy_refusal_retries_with_direct_connection(self):
        expected = (b"%PDF-1.4\n%%EOF", "application/pdf", "https://publisher.example/paper.pdf")
        with patch("app.services.institutional_access.service._validate_public_url"), patch(
            "app.services.institutional_access.service._read_request",
            side_effect=[URLError("[WinError 10061] actively refused"), expected],
        ) as reader:
            result = _request_bytes(
                "https://publisher.example/paper.pdf", max_bytes=1024, timeout=2
            )
        self.assertEqual(result, expected)
        self.assertEqual(reader.call_count, 2)
        self.assertFalse(reader.call_args_list[0].kwargs["direct"])
        self.assertTrue(reader.call_args_list[1].kwargs["direct"])

    def test_failed_caj_conversion_leaves_no_caj_asset(self):
        root = Path("storage/runtime/test-artifacts") / uuid4().hex
        root.mkdir(parents=True)
        try:
            with patch(
                "app.services.institutional_access.service._convert_caj_to_pdf",
                return_value=False,
            ):
                with self.assertRaises(InstitutionalAccessError) as context:
                    _persist_pdf_only(
                        b"CAJViewer" + b"0" * 2048,
                        "caj",
                        root,
                        "Conversion failure",
                        "https://kns.cnki.net/article",
                    )
                self.assertEqual(context.exception.code, "CAJ_CONVERSION_FAILED")
                self.assertEqual(list(root.iterdir()), [])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    async def test_browser_caj_is_converted_and_only_pdf_is_saved(self):
        root = Path("storage/runtime/test-artifacts") / uuid4().hex
        root.mkdir(parents=True)
        try:
            settings = SimpleNamespace(storage_dir=root)
            user = UserContext(tenant_id="tenant_caj", user_id="user_caj")
            session_id = "session_caj"
            source = (
                root / "browser-downloads" / user.tenant_id / user.user_id
                / session_id / "paper.caj"
            )
            source.parent.mkdir(parents=True)
            source.write_bytes(b"CAJViewer" + b"0" * 4096)

            def convert(_source: Path, destination: Path) -> bool:
                destination.write_bytes(b"%PDF-1.4\n" + b"0" * 4096)
                return True

            saver = AsyncMock(side_effect=lambda paper: paper.to_dict())
            with patch(
                "app.services.institutional_access.service.get_settings",
                return_value=settings,
            ), patch(
                "app.services.institutional_access.service._convert_caj_to_pdf",
                side_effect=convert,
            ), patch(
                "app.services.institutional_access.service._validate_article_pdf",
            ), patch(
                "app.services.institutional_access.service._extract_pdf_text",
                return_value="converted paper text",
            ), patch(
                "mcp_server.scholar_mcp.store.knowledge_store.save_paper",
                saver,
            ):
                saved = await institutional_access_service.ingest_browser_download(
                    user,
                    session_id,
                    {
                        "title": "Converted paper",
                        "file_path": str(source),
                        "detail_url": "https://download.example/paper.caj",
                    },
                )

            stored = Path(saved["file_path"])
            self.assertEqual(stored.suffix.lower(), ".pdf")
            self.assertTrue(stored.exists())
            self.assertFalse(source.exists())
            self.assertEqual(saved["metadata"]["document_format"], "pdf")
            self.assertEqual(saved["metadata"]["content_type"], "application/pdf")
            self.assertTrue(saved["metadata"]["converted_from_caj"])
            self.assertNotIn("original_file_path", saved["metadata"])
            self.assertEqual(saved["url"], "")
            self.assertEqual(list(root.rglob("*.caj")), [])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_caj_download_url_is_not_stored_as_knowledge_source(self):
        self.assertEqual(_knowledge_source_url("https://example.test/paper.caj"), "")
        self.assertEqual(
            _knowledge_source_url("https://kns.cnki.net/kcms2/article/abstract?v=1"),
            "https://kns.cnki.net/kcms2/article/abstract?v=1",
        )

    async def test_confirmed_pdf_is_validated_and_saved_to_knowledge(self):
        with patch("app.services.institutional_access.service._validate_public_url"):
            plan = institutional_access_service.prepare_download(
                self.user,
                session_id=self.session["session_id"],
                source_url="https://publisher.example/paper.pdf",
                title="Institution Download Test",
                doi="10.1000/institution-test",
                source="institution",
            )
        with self.assertRaises(InstitutionalAccessError) as context:
            await institutional_access_service.confirm_download(
                self.user, plan["download_id"], confirmation_token=""
            )
        self.assertEqual(context.exception.code, "DOWNLOAD_CONFIRMATION_REQUIRED")

        fake_pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF"
        with patch(
            "app.services.institutional_access.service._request_bytes",
            return_value=(fake_pdf, "application/pdf", "https://publisher.example/paper.pdf"),
        ), patch("app.services.institutional_access.service._validate_article_pdf"):
            result = await institutional_access_service.confirm_download(
                self.user,
                plan["download_id"],
                confirmation_token="confirmed-call",
            )

        self.assertEqual(result["download"]["status"], "completed")
        self.assertEqual(result["download"]["file_type"], "pdf")
        paper_id = result["paper"]["paper_id"]
        papers = await knowledge_store.search(self.user.tenant_id, self.user.user_id, paper_id, 5)
        self.assertTrue(any(item["paper_id"] == paper_id for item in papers))


if __name__ == "__main__":
    unittest.main()
