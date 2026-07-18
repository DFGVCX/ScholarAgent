from pathlib import Path
import unittest


class ModelSettingsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = Path("frontend/dist/app.html").read_text(encoding="utf-8")

    def test_settings_ui_uses_postgres_pgvector_and_qwen_only(self) -> None:
        self.assertIn("PostgreSQL 17 + pgvector", self.html)
        self.assertIn('value="qwen"', self.html)
        self.assertIn("测试 Agent 模型", self.html)
        self.assertIn("测试 Embedding", self.html)
        self.assertIn("重新生成向量", self.html)
        for legacy in ("MySQL URL", "JSON 文件", "Jina Embeddings", "Cohere Embeddings"):
            self.assertNotIn(legacy, self.html)

    def test_candidate_values_are_sent_to_probe_routes(self) -> None:
        self.assertIn("/settings/model/probe", self.html)
        self.assertIn("/settings/embedding/probe", self.html)
        self.assertIn("/settings/embedding/reindex", self.html)
        for control in (
            "cfgPrimaryProvider",
            "cfgLlmBaseUrl",
            "cfgLlmApiKey",
            "cfgLlmModel",
            "cfgRagEmbeddingBaseUrl",
            "cfgRagEmbeddingApiKey",
            "cfgRagEmbeddingModel",
        ):
            self.assertIn(control, self.html)

    def test_console_bridge_cache_busts_the_no_store_html(self) -> None:
        bridge = Path("frontend/src/app/LegacyConsoleBridge.tsx").read_text(encoding="utf-8")
        self.assertIn("frameVersion", bridge)
        self.assertIn("/app.html?v=", bridge)

    def test_rag_console_renders_canonical_chunk_fields(self) -> None:
        self.assertIn("<th>Chunk</th><th>命中片段</th><th>来源论文</th>", self.html)
        self.assertIn("Chunk #${Number(item.chunk_index ?? 0)}", self.html)
        self.assertIn("escapeHtml(item.chunk_id || '-')", self.html)
        self.assertIn("escapeHtml(item.title || item.paper_id)", self.html)
        self.assertIn("item.lexical_rank ?? '-'", self.html)
        self.assertIn("item.vector_rank ?? '-'", self.html)
        self.assertIn("escapeHtml(item.snippet || '')", self.html)

    def test_rag_console_renders_complete_chunk_text(self) -> None:
        self.assertIn(
            "<div class=\"rag-verify-snippet\">${escapeHtml(item.snippet || '')}</div>",
            self.html,
        )
        self.assertNotIn("escapeHtml(item.snippet || '').slice(", self.html)


if __name__ == "__main__":
    unittest.main()
