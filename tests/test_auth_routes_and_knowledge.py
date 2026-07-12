from __future__ import annotations

import io
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.routes.auth import LoginRequestDTO, login, me
from app.routes.knowledge import (
    FileAnnotationDTO,
    FileTextUpdateDTO,
    KnowledgePaperDTO,
    delete_knowledge,
    get_file_annotations,
    get_knowledge_file,
    list_knowledge,
    save_file_annotations,
    save_file_text,
    save_knowledge,
    upload_knowledge_file,
)
from app.services.rag_service import rag_service


class AuthRoutesAndKnowledgeTest(unittest.IsolatedAsyncioTestCase):
    async def test_login_and_me_return_tenant_context(self):
        profile = await login(
            LoginRequestDTO(username="acme", password="acme123", tenant_id="tenant_acme")
        )

        self.assertEqual(profile["tenant_id"], "tenant_acme")
        self.assertEqual(profile["access_token"], "acme-key")

        current = await me(profile["access_token"])

        self.assertEqual(current["user_id"], "user_acme")

    async def test_knowledge_crud_is_tenant_scoped_and_delete_requires_confirmation(self):
        title = f"Tenant scoped citation memory {uuid4()}"
        create = await save_knowledge(
            KnowledgePaperDTO(
                source="manual",
                title=title,
                authors=["ScholarAgent"],
                abstract="Local tenant-scoped knowledge item.",
            ),
            x_api_key="demo-key",
        )
        paper_id = create["item"]["paper_id"]

        demo_list = await list_knowledge(query=title, source="local", limit=50, x_api_key="demo-key")
        self.assertTrue(any(item["paper_id"] == paper_id for item in demo_list["items"]))

        # Query the unique title so this assertion remains stable when a developer's
        # local index already contains many earlier test documents with the same prefix.
        rag_search = await rag_service.search("tenant_demo", "user_demo", title, 5)
        self.assertTrue(any(item["paper_id"] == paper_id for item in rag_search["items"]))
        rag_stats = await rag_service.stats("tenant_demo", "user_demo")
        self.assertGreaterEqual(rag_stats["chunk_count"], 1)

        acme_list = await list_knowledge(query=title, source="local", limit=50, x_api_key="acme-key")
        self.assertFalse(any(item["paper_id"] == paper_id for item in acme_list["items"]))

        delete_without_token = await delete_knowledge(paper_id, x_api_key="demo-key")
        self.assertEqual(delete_without_token["status"], "REQUIRE_CONFIRM")

        delete_with_token = await delete_knowledge(
            paper_id,
            confirmation_token="confirm-delete",
            x_api_key="demo-key",
        )
        self.assertTrue(delete_with_token["deleted"])

    async def test_upload_knowledge_file_creates_tenant_scoped_file_preview(self):
        title = f"Uploaded knowledge file {uuid4()}"
        upload = UploadFile(
            filename="uploaded-note.txt",
            file=io.BytesIO(b"retrieval augmented generation note for viewer"),
        )

        create = await upload_knowledge_file(
            file=upload,
            title=title,
            authors="ScholarAgent",
            source="manual",
            published_at="",
            doi="",
            arxiv_id="",
            url="",
            abstract="",
            x_api_key="demo-key",
        )

        item = create["item"]
        self.assertEqual(item["title"], title)
        self.assertIn("file_url", item["metadata"])
        self.assertIn("retrieval augmented generation", item["full_text"])

        response = await get_knowledge_file(item["paper_id"], api_key="demo-key", x_api_key=None)
        stored_path = Path(item["metadata"]["file_path"])
        self.assertTrue(stored_path.exists())
        self.assertEqual(response.status_code, 200)
        self.assertIn("inline", response.headers["content-disposition"])

        annotations = await save_file_annotations(
            item["paper_id"],
            FileAnnotationDTO(strokes=[{"points": [[0.1, 0.2], [0.2, 0.3]], "color": "#0a7a70"}]),
            x_api_key="demo-key",
        )
        self.assertTrue(annotations["saved"])
        loaded_annotations = await get_file_annotations(item["paper_id"], api_key="demo-key", x_api_key=None)
        self.assertEqual(len(loaded_annotations["strokes"]), 1)

        text_update = await save_file_text(
            item["paper_id"],
            FileTextUpdateDTO(content="updated markdown compatible note for inline editor"),
            x_api_key="demo-key",
        )
        self.assertTrue(text_update["saved"])
        self.assertEqual(stored_path.read_text(encoding="utf-8"), "updated markdown compatible note for inline editor")

        blocked = await list_knowledge(query=title, source="local", limit=10, x_api_key="acme-key")
        self.assertFalse(any(row["paper_id"] == item["paper_id"] for row in blocked["items"]))

        delete_with_token = await delete_knowledge(
            item["paper_id"],
            confirmation_token="confirm-delete",
            x_api_key="demo-key",
        )
        self.assertTrue(delete_with_token["deleted"])


if __name__ == "__main__":
    unittest.main()
