from __future__ import annotations

import unittest
from urllib.error import URLError
from unittest.mock import patch
from uuid import uuid4

from app.schemas import UserContext
from app.services.institutional_access.service import (
    InstitutionalAccessError,
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
        ):
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
