from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Keep this list free of tests that require Docker, a live PostgreSQL database,
# external model APIs, or a browser. Those belong to the separately documented
# integration/E2E gate.
RAG_TEST_TARGETS: tuple[str, ...] = (
    "tests.test_alembic_revisions",
    "tests.test_db_event_loop",
    "tests.test_docling_models",
    "tests.test_papers_package",
    "tests.test_formula_parsing",
    "tests.test_hierarchical_parsing",
    "tests.test_multimodal_parsing",
    "tests.test_object_quality",
    "tests.test_paper_assets",
    "tests.test_paper_chunking",
    "tests.test_paper_ingestion",
    "tests.test_pdf_ingestion_queue",
    "tests.test_paper_metadata",
    "tests.test_knowledge_metadata",
    "tests.test_paper_repository",
    "tests.test_qwen_embedding",
    "tests.test_embedding_usage",
    "tests.test_embedding_lifecycle",
    "tests.test_reembedding_service",
    "tests.test_retrieval_service",
    "tests.test_qwen_reranker",
    "tests.test_retrieval_replay",
    "tests.test_retrieval_reproducibility",
    "tests.test_retrieval_performance",
    "tests.test_retrieval_strategy",
    "tests.test_retrieval_evaluation",
    "tests.test_production_retrieval_evaluation",
    "tests.test_rag_stats",
    "tests.test_hybrid_retrieval",
    "tests.test_postgres_config",
    "tests.test_postgres_health",
    "tests.test_postgres_store",
    "tests.test_postgres_operations",
    "tests.test_runtime_config",
    "tests.test_settings_routes",
    "tests.test_model_settings_ui",
    "tests.test_rag_regression_manifest",
)


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromNames(RAG_TEST_TARGETS)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
