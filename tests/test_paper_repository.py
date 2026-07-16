from __future__ import annotations

import unittest

from app.papers.models import PaperInput, normalize_arxiv_id, normalize_doi
from app.papers.repository import PaperRepository


class _Mappings:
    def first(self):
        return None


class _Result:
    def mappings(self):
        return _Mappings()


class _Session:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), params or {}))
        return _Result()


class PaperRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_requires_tenant_user_and_not_deleted(self) -> None:
        session = _Session()
        paper = await PaperRepository(session).get("tenant-a", "user-a", "paper-1")

        self.assertIsNone(paper)
        sql, params = session.statements[-1]
        self.assertIn("tenant_id", sql)
        self.assertIn("user_id", sql)
        self.assertIn("deleted_at IS NULL", sql)
        self.assertEqual(params["tenant_id"], "tenant-a")
        self.assertEqual(params["user_id"], "user-a")

    def test_identifier_normalization(self) -> None:
        self.assertEqual(normalize_doi(" https://doi.org/10.1000/ABC.1 "), "10.1000/abc.1")
        self.assertEqual(normalize_arxiv_id("arXiv:2401.12345v2"), "2401.12345")

    def test_paper_input_rejects_empty_identity(self) -> None:
        with self.assertRaises(ValueError):
            PaperInput(paper_id="", source="manual", title="A paper")


if __name__ == "__main__":
    unittest.main()
