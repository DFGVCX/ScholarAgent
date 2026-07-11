from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from browser_worker.cnki_adapter import _is_article_pdf, search_cnki
from browser_worker.manager import _safe_segment


class _FakeLocator:
    async def evaluate_all(self, script):
        return [
            {
                "title": "  多智能体科研写作研究  ",
                "url": "https://kns.cnki.net/kcms2/article/abstract?v=1",
                "container": "作者 期刊 2025 多智能体科研写作研究",
            },
            {
                "title": "多智能体科研写作研究",
                "url": "https://kns.cnki.net/kcms2/article/abstract?v=1",
                "container": "duplicate",
            },
        ]


class _FakePage:
    def __init__(self):
        self.url = ""

    async def goto(self, url, **kwargs):
        self.url = url

    async def wait_for_load_state(self, *args, **kwargs):
        return None

    def locator(self, selector):
        return _FakeLocator()


class BrowserWorkerTest(unittest.IsolatedAsyncioTestCase):
    async def test_cnki_search_normalizes_and_deduplicates_results(self):
        page = _FakePage()
        items = await search_cnki(page, "多智能体 科研写作", 20)
        self.assertIn("kw=", page.url)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "多智能体科研写作研究")
        self.assertEqual(items[0]["year"], "2025")

    def test_browser_identity_segments_reject_empty_values(self):
        self.assertEqual(_safe_segment("tenant_demo"), "tenant_demo")
        with self.assertRaises(ValueError):
            _safe_segment("***")

    def test_cnki_ad_pdf_is_rejected(self):
        path = Path("storage/runtime/test-artifacts") / f"{uuid4().hex}.pdf"
        self.assertFalse(_is_article_pdf(path, "https://a.cnki.net/gw/api/get/pdf/ads/v1/pdf/demo.pdf"))

    def test_short_placeholder_pdf_is_rejected(self):
        root = Path("storage/runtime/test-artifacts")
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{uuid4().hex}.pdf"
        try:
            path.write_bytes(b"%PDF-1.4\n" + b"0" * 30_000)
            page = type("Page", (), {"extract_text": lambda self: "1 / 1 绘制"})()
            reader = type("Reader", (), {"pages": [page]})()
            with patch("pypdf.PdfReader", return_value=reader):
                self.assertFalse(_is_article_pdf(path, "https://kns.cnki.net/paper.pdf"))
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
