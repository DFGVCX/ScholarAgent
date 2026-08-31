from __future__ import annotations

import unittest

from scripts.run_rag_regression import RAG_TEST_TARGETS


class RagRegressionManifestTest(unittest.TestCase):
    def test_manifest_covers_core_rag_layers(self) -> None:
        required = {
            "tests.test_paper_chunking",
            "tests.test_paper_repository",
            "tests.test_qwen_embedding",
            "tests.test_retrieval_service",
            "tests.test_retrieval_evaluation",
            "tests.test_rag_stats",
            "tests.test_settings_routes",
        }

        self.assertTrue(required.issubset(set(RAG_TEST_TARGETS)))

    def test_manifest_excludes_external_database_and_browser_suites(self) -> None:
        excluded = {
            "tests.test_phase2_knowledge",
            "tests.test_auth_routes_and_knowledge",
            "tests.test_paper_acquisition",
            "tests.e2e",
        }

        self.assertTrue(excluded.isdisjoint(set(RAG_TEST_TARGETS)))


if __name__ == "__main__":
    unittest.main()
