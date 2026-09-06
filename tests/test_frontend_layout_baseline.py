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
        self.assertIn('/assets/layout-baseline.v1.css?v=2', html)

    def test_source_and_deployed_baselines_are_identical(self) -> None:
        self.assertTrue(SOURCE_BASELINE.is_file())
        self.assertTrue(DIST_BASELINE.is_file())
        self.assertEqual(
            SOURCE_BASELINE.read_text(encoding="utf-8"),
            DIST_BASELINE.read_text(encoding="utf-8"),
        )

    def test_rag_styles_are_scoped_and_deployed_with_tab_controls(self) -> None:
        source = ROOT / 'frontend' / 'public' / 'assets' / 'profile-rag.css'
        deployed = ROOT / 'frontend' / 'dist' / 'assets' / 'profile-rag.css'
        self.assertEqual(source.read_text(encoding='utf-8'), deployed.read_text(encoding='utf-8'))
        html = APP_HTML.read_text(encoding='utf-8')
        self.assertIn('/assets/profile-rag.css?v=1', html)
        for name in ('Embedding', 'Documents', 'Retrieval', 'Index', 'Search'):
            self.assertIn(f'aria-controls="ragPane{name}"', html)
            self.assertIn(f'aria-labelledby="ragTab{name}"', html)

    def test_reader_geometry_allows_sidebars_to_collapse(self) -> None:
        css = SOURCE_BASELINE.read_text(encoding='utf-8')
        self.assertIn('var(--reader-left-width, 176px)', css)
        self.assertIn('var(--reader-right-width, 260px)', css)
        self.assertNotIn('224px minmax(440px, 1fr) 360px !important', css)

    def test_metadata_styles_are_deployed_and_scoped_to_knowledge(self) -> None:
        source = ROOT / 'frontend' / 'public' / 'assets' / 'paper-metadata.css'
        deployed = ROOT / 'frontend' / 'dist' / 'assets' / 'paper-metadata.css'
        css = source.read_text(encoding='utf-8')
        self.assertEqual(css, deployed.read_text(encoding='utf-8'))
        self.assertIn('/assets/paper-metadata.css?v=1', APP_HTML.read_text(encoding='utf-8'))
        self.assertIn('#pageKnowledge .reader-shell.metadata-mode .reader-tools', css)
        self.assertIn('grid-template-rows: auto minmax(0, 1fr) auto', css)
        self.assertNotIn('position: sticky', css)

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
