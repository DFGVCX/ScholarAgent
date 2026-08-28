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

    def test_runtime_settings_expose_all_pdf_and_chunk_strategies(self) -> None:
        with patch.object(runtime_config, "read_runtime_config", return_value={}), patch(
            "app.services.mysql_store.configured_database_name", return_value="scholar_agent"
        ):
            payload = runtime_config.public_runtime_config()

        fields = {item["key"]: item for item in payload["items"]}
        self.assertIn("SCHOLAR_PDF_PARSE_STRATEGY", fields)
        self.assertIn("scholar_hierarchical_v4", fields["SCHOLAR_PDF_PARSE_STRATEGY"]["options"])
        self.assertIn("scholar_hierarchical_v4", fields["SCHOLAR_RAG_CHUNK_STRATEGY"]["options"])
        self.assertIn("multimodal_aware_v3", fields["SCHOLAR_RAG_CHUNK_STRATEGY"]["options"])


if __name__ == "__main__":
    unittest.main()
