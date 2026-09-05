from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
SOURCE_BASELINE = ROOT / "frontend" / "public" / "assets" / "layout-baseline.v1.css"
DIST_BASELINE = ROOT / "frontend" / "dist" / "assets" / "layout-baseline.v1.css"
APP_HTML = ROOT / "frontend" / "dist" / "app.html"


class FrontendLayoutBaselineTests(unittest.TestCase):
    def test_versioned_baseline_is_loaded_by_the_console(self) -> None:
        html = APP_HTML.read_text(encoding="utf-8")
        self.assertIn('data-layout-baseline="v1"', html)
        self.assertIn('/assets/layout-baseline.v1.css?v=1', html)

    def test_source_and_deployed_baselines_are_identical(self) -> None:
        self.assertTrue(SOURCE_BASELINE.is_file())
        self.assertTrue(DIST_BASELINE.is_file())
        self.assertEqual(
            SOURCE_BASELINE.read_text(encoding="utf-8"),
            DIST_BASELINE.read_text(encoding="utf-8"),
        )

    def test_every_work_page_has_an_explicit_layout_scope(self) -> None:
        css = SOURCE_BASELINE.read_text(encoding="utf-8")
        for page_id in (
            "#pageChat",
            "#pageTasks",
            "#pageAudit",
            "#pageKnowledge",
            "#pageProfile",
            "#pageReader",
        ):
            self.assertIn(page_id, css)
        self.assertIn("--layout-module-rail: minmax(268px, 302px);", css)
        self.assertIn("--layout-module-gap: 14px;", css)


if __name__ == "__main__":
    unittest.main()
