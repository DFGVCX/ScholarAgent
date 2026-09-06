"""Offline contract tests. External service acceptance is deliberately separate."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MODULES = (
    "tests.test_capability_completion",
    "tests.test_retrieval_service",
    "tests.test_retrieval_reproducibility",
    "tests.test_retrieval_replay",
    "tests.test_retrieval_evaluation",
    "tests.test_skill_registry",
    "tests.test_citation_guard",
    "tests.test_model_configuration",
    "tests.test_model_factory_endpoints",
    "tests.test_mcp_registry",
    "tests.test_dynamic_task_graph",
    "tests.test_langfuse_tracing",
)


def main() -> int:
    # Do not query a developer's database while importing runtime defaults.
    with patch("app.services.mysql_store.get_all_settings", return_value={}), \
         patch("app.services.mysql_store.is_available", return_value=False):
        suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return int(not result.wasSuccessful())


if __name__ == "__main__":
    raise SystemExit(main())
