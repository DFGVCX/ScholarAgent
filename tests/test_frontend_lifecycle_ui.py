from __future__ import annotations

from pathlib import Path
import unittest


class FrontendLifecycleUiTests(unittest.TestCase):
    @staticmethod
    def _html() -> str:
        return (Path(__file__).parents[1] / "frontend" / "dist" / "app.html").read_text(
            encoding="utf-8"
        )

    def test_original_workflow_ui_accepts_dynamic_lifecycle_events(self) -> None:
        html = self._html()
        for contract in (
            '<h2>生成流程</h2>',
            'data-phase-key="ingest_sources"',
            'data-phase-key="citation_format"',
            "function applyWorkflowEvent",
            'id="reviewState"',
            "function setWorkflowReview",
            "function completeWorkflowReview",
            "targeted_retry: '局部回退'",
            "literature_retrieval:",
        ):
            self.assertIn(contract, html)

    def test_compact_panels_support_scroll_and_expansion(self) -> None:
        html = self._html()
        for contract in (
            'id="auditPoolToggle"',
            "function toggleAuditPool",
            "audit-pool-expanded",
            "overflow-y: auto;",
            "grid-auto-rows: 118px;",
            "grid-template-columns: repeat(3, minmax(0, 1fr));",
            "#pageTasks .workflow-panel .lane-step span span",
        ):
            self.assertIn(contract, html)


if __name__ == "__main__":
    unittest.main()
