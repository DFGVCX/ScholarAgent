from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services import runtime_config


class RuntimeConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_config._RUNTIME_CONFIG_CACHE = None

    def tearDown(self) -> None:
        runtime_config._RUNTIME_CONFIG_CACHE = None

    def test_failed_database_read_is_not_cached(self) -> None:
        saved = {
            "SCHOLAR_RAG_EMBEDDING_MODEL": "qwen3.7-text-embedding",
            "SCHOLAR_RAG_EMBEDDING_API_KEY": "sk-test",
        }
        with patch(
            "app.services.mysql_store.get_all_settings",
            side_effect=[RuntimeError("database is starting"), saved],
        ) as load:
            self.assertEqual(runtime_config.read_runtime_config(), {})
            self.assertEqual(runtime_config.read_runtime_config(), saved)

        self.assertEqual(load.call_count, 2)

    def test_empty_startup_read_is_not_cached(self) -> None:
        saved = {"SCHOLAR_RAG_EMBEDDING_MODEL": "qwen3.7-text-embedding"}
        with patch(
            "app.services.mysql_store.get_all_settings",
            side_effect=[{}, saved],
        ) as load:
            self.assertEqual(runtime_config.read_runtime_config(), {})
            self.assertEqual(runtime_config.read_runtime_config(), saved)

        self.assertEqual(load.call_count, 2)

    def test_update_preserves_blank_secret_when_cache_is_empty(self) -> None:
        saved = {
            "SCHOLAR_RAG_EMBEDDING_API_KEY": "sk-saved",
            "SCHOLAR_RAG_EMBEDDING_MODEL": "qwen3.7-text-embedding",
        }
        runtime_config._RUNTIME_CONFIG_CACHE = {}

        with patch(
            "app.services.mysql_store.get_all_settings", return_value=saved
        ), patch("app.services.mysql_store.set_setting") as set_setting, patch(
            "app.services.mysql_store.execute"
        ) as execute:
            updated = runtime_config.update_runtime_config(
                {
                    "SCHOLAR_RAG_EMBEDDING_API_KEY": "",
                    "SCHOLAR_RAG_TOP_K": "6",
                }
            )

        self.assertEqual(updated["SCHOLAR_RAG_EMBEDDING_API_KEY"], "sk-saved")
        self.assertEqual(updated["SCHOLAR_RAG_EMBEDDING_MODEL"], "qwen3.7-text-embedding")
        self.assertEqual(updated["SCHOLAR_RAG_TOP_K"], "6")
        set_setting.assert_any_call("SCHOLAR_RAG_EMBEDDING_API_KEY", "sk-saved")
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
