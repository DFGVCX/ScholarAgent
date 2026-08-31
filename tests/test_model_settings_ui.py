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

    def test_rag_console_explains_semantic_fallback(self) -> None:
        self.assertIn("const warnings = data.warnings || [];", self.html)
        self.assertIn("向量检索暂不可用，当前结果来自关键词与中英文学术术语兜底", self.html)

    def test_runtime_config_cannot_save_before_successful_load(self) -> None:
        self.assertIn(
            'id="saveRuntimeConfigBtn" class="primary" type="button" disabled',
            self.html,
        )
        self.assertIn("if (!state.runtimeConfig)", self.html)

    def test_rag_settings_can_select_hierarchical_parser_and_chunker(self) -> None:
        self.assertIn('id="cfgPdfParseStrategy"', self.html)
        self.assertIn('data-config-key="SCHOLAR_PDF_PARSE_STRATEGY"', self.html)
        self.assertIn('id="cfgRagChunkStrategy"', self.html)
        self.assertIn('data-config-key="SCHOLAR_RAG_CHUNK_STRATEGY"', self.html)
        self.assertGreaterEqual(self.html.count('value="scholar_hierarchical_v4"'), 2)
        self.assertIn("Docling 主解析 + PyMuPDF 自动回退", self.html)

    def test_rag_console_renders_chunk_section_and_page_provenance(self) -> None:
        self.assertIn("ragChunkProvenance(item)", self.html)
        self.assertIn("item.section_path || item.section_id", self.html)
        self.assertIn("item.page_start", self.html)
        self.assertIn("item.page_end", self.html)
        self.assertIn("item.chunk_type", self.html)
        self.assertIn("公式", self.html)
        self.assertIn("表格", self.html)

    def test_paper_workbench_renders_structured_visual_blocks(self) -> None:
        self.assertIn('data-preview-mode="assets">图表算法</button>', self.html)
        self.assertIn("/structure`", self.html)
        self.assertIn("function renderStructuredPaper", self.html)
        self.assertIn("function renderPaperVisualLibrary", self.html)
        self.assertIn("function paperAssetUrl", self.html)
        self.assertIn("data-visual-filter=\"figure\"", self.html)
        self.assertIn("data-visual-filter=\"table\"", self.html)
        self.assertIn("data-visual-filter=\"algorithm\"", self.html)
        self.assertIn("metadata.markdown", self.html)
        self.assertIn("paper-visual-image", self.html)
        self.assertIn("暂无结构块，回退到解析全文", self.html)

    def test_formula_debug_source_is_visible_even_when_katex_renders(self) -> None:
        self.assertIn('class="paper-equation-debug"', self.html)
        self.assertIn("paper-equation-source", self.html)
        self.assertIn("function renderEquationBlock", self.html)
        self.assertIn("paper-equation-crop", self.html)

    def test_visual_debug_assets_are_loaded_eagerly(self) -> None:
        self.assertIn('loading="eager"', self.html)
        self.assertNotIn(
            'class="paper-visual-image" src="${escapeHtml(asset)}" alt="${escapeHtml(label)}" loading="lazy"',
            self.html,
        )
        self.assertIn(".paper-visual-card > .markdown-preview { min-width: 0; }", self.html)

    def test_structured_paper_views_use_the_existing_scroll_host(self) -> None:
        self.assertIn(
            "viewer.classList.toggle('structured-content', ['text', 'assets', 'chunks', 'metadata'].includes(state.paperViewMode));",
            self.html,
        )
        self.assertIn(
            ".reader-canvas.structured-content { overflow-y: auto; overflow-x: hidden; }",
            self.html,
        )
        self.assertIn(
            'viewer.innerHTML = `<div class="parsed-editor-stage">${renderStructuredPaper(item, structure)}</div>`;',
            self.html,
        )
        self.assertIn(
            'viewer.innerHTML = `<div class="parsed-editor-stage">${renderPaperVisualLibrary(item, structure)}</div>`;',
            self.html,
        )
        self.assertIn('data-preview-mode="chunks">切片</button>', self.html)
        self.assertIn("window.ScholarChunkView.render(structure, state.paperChunkFilter)", self.html)

    def test_paper_workbench_exposes_auditable_editable_bibliography(self) -> None:
        self.assertIn('data-preview-mode="metadata">论文信息</button>', self.html)
        self.assertIn("function renderPaperMetadata", self.html)
        self.assertIn("function bibliographyReviewSummary", self.html)
        self.assertIn("完整字段", self.html)
        self.assertIn("待修正", self.html)
        self.assertIn("function savePaperMetadata", self.html)
        for field in (
            "title",
            "title_translation",
            "authors",
            "institutions",
            "published_at",
            "venue",
            "doi",
            "arxiv_id",
            "paper_type",
        ):
            self.assertIn(f"['{field}',", self.html)
        self.assertIn('data-bibliography-field="links"', self.html)
        self.assertIn("field.source || 'not_found'", self.html)
        self.assertIn("field.confidence", self.html)
        self.assertIn("field.user_edited", self.html)
        self.assertIn("savePaperMetadataBtn", self.html)
        self.assertIn("`/knowledge/${encodeURIComponent(item.paper_id)}/metadata`", self.html)
        self.assertIn("method: 'PATCH'", self.html)
        self.assertNotIn("updated_from: 'web_metadata_editor'", self.html)


if __name__ == "__main__":
    unittest.main()
