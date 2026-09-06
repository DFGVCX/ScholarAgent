from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.routes.knowledge import _find_user_paper
from app.papers.repository import PaperRepository


class KnowledgeFileLookupTests(unittest.IsolatedAsyncioTestCase):
    async def lookup(self, document):
        user = SimpleNamespace(tenant_id="tenant_a", user_id="user_a")
        session = object()

        @asynccontextmanager
        async def transaction(tenant_id, user_id):
            self.assertEqual((tenant_id, user_id), ("tenant_a", "user_a"))
            yield session

        with patch("app.routes.knowledge.tenant_transaction", transaction), patch(
            "app.routes.knowledge.PaperRepository"
        ) as repository, patch("app.routes.knowledge.ScholarMCPClient") as search:
            repository.return_value.get_document = AsyncMock(return_value=document)
            try:
                return await _find_user_paper("paper:pdf:abc123", user)
            finally:
                repository.assert_called_once_with(session)
                repository.return_value.get_document.assert_awaited_once_with(
                    "tenant_a", "user_a", "paper:pdf:abc123"
                )
                search.assert_not_called()

    async def test_file_lookup_does_not_require_searchable_content(self):
        document = {
            "paper_id": "paper:pdf:abc123",
            "full_text": "",
            "ingestion_status": "queued",
            "metadata": {"file_path": "uploads/tenant_a/user_a/paper.pdf"},
        }
        self.assertEqual(await self.lookup(document), document)

    async def test_missing_or_other_users_document_is_not_returned(self):
        with self.assertRaises(HTTPException) as raised:
            await self.lookup(None)
        self.assertEqual(raised.exception.status_code, 404)

    async def test_document_repository_binds_exact_id_without_fuzzy_query(self):
        result = MagicMock()
        result.mappings.return_value.all.return_value = []
        session = SimpleNamespace(execute=AsyncMock(return_value=result))
        self.assertIsNone(await PaperRepository(session).get_document('t', 'u', 'paper:pdf:abc'))
        statement, params = session.execute.await_args.args
        self.assertIn('p.paper_id=:paper_id', str(statement))
        self.assertIn('p.deleted_at IS NULL', str(statement))
        self.assertEqual(params['paper_id'], 'paper:pdf:abc')
        self.assertEqual(params['query'], '')
        self.assertEqual(params['limit'], 1)
        self.assertEqual((params['tenant_id'], params['user_id']), ('t', 'u'))


if __name__ == "__main__":
    unittest.main()
