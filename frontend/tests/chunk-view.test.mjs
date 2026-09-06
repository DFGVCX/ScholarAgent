import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const productionPath = new URL('../dist/chunk-view.js', import.meta.url);

function loadChunkView() {
  if (!existsSync(productionPath)) return null;
  const window = {};
  vm.runInNewContext(readFileSync(productionPath, 'utf8'), { window });
  return window.ScholarChunkView;
}

const structure = {
  content_version: 15,
  chunker: { strategy: 'scholar_hierarchical_v4', version: '4' },
  chunks: [
    {
      id: 'chunk-prose',
      index: 0,
      type: 'prose',
      section_path: 'II. Method > Setup',
      parent_section_id: 'method',
      page_start: 2,
      page_end: 3,
      content: 'Complete source <script>alert(1)</script>\nTAIL_MARKER_MUST_REMAIN_VISIBLE',
      character_count: 72,
      token_count: 15,
      source_block_ids: ['page-2-block-1', 'page-3-block-1'],
      embedding_status: 'ready',
      embedding_model: 'qwen3.7-text-embedding',
      context_before: 'Definition before the atomic object.',
      context_after: 'Explanation after the atomic object.',
      previous_chunk_id: null,
      next_chunk_id: 'chunk-equation',
    },
    {
      id: 'chunk-equation',
      index: 1,
      type: 'equation',
      section_path: 'II. Method > Equation 1',
      page_start: 3,
      page_end: 3,
      content: '$$F(x)=x^2$$',
      character_count: 14,
      token_count: 6,
      source_block_ids: ['page-3-equation-1'],
      embedding_status: 'ready',
      embedding_model: 'qwen3.7-text-embedding',
      parent_section_id: 'method',
      previous_chunk_id: 'chunk-prose',
      next_chunk_id: null,
      metadata: {
        object_quality: {
          version: 'v1',
          status: 'review',
          score: 0.55,
          reasons: ['source_marked_review'],
          checks: { structured_content_available: true },
        },
      },
    },
  ],
};

test('renders every chunk with complete escaped source text and provenance', () => {
  const chunkView = loadChunkView();
  assert.ok(chunkView, 'chunk-view renderer must be available to the paper workbench');

  const html = chunkView.render(structure, 'all');

  assert.equal((html.match(/data-chunk-index=/g) || []).length, 2);
  assert.match(html, /TAIL_MARKER_MUST_REMAIN_VISIBLE/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>alert\(1\)<\/script>/);
  assert.match(html, /II\. Method &gt; Setup/);
  assert.match(html, /第 2–3 页/);
  assert.match(html, /scholar_hierarchical_v4/);
  assert.match(html, /来源块 2/);
});

test('filters primary cards by type while retaining adjacent context preview', () => {
  const chunkView = loadChunkView();
  assert.ok(chunkView, 'chunk-view renderer must be available to the paper workbench');

  const html = chunkView.render(structure, 'equation');

  assert.equal((html.match(/data-chunk-index=/g) || []).length, 1);
  assert.match(html, /\$\$F\(x\)=x\^2\$\$/);
  assert.match(html, /TAIL_MARKER_MUST_REMAIN_VISIBLE/);
});

test('renders auditable parent and adjacent chunk context without replacing source text', () => {
  const chunkView = loadChunkView();
  assert.ok(chunkView);

  const html = chunkView.render(structure, 'all');

  assert.match(html, /父章节 method/);
  assert.match(html, /相邻 Chunk/);
  assert.match(html, /连续上下文预览/);
  assert.match(html, /Definition before the atomic object/);
  assert.match(html, /Explanation after the atomic object/);
  assert.match(html, /chunk-equation/);
});

test('renders typed object quality status, reasons, and checks', () => {
  const chunkView = loadChunkView();
  assert.ok(chunkView);

  const html = chunkView.render(structure, 'equation');

  assert.match(html, /对象质量 review/);
  assert.match(html, /0\.55/);
  assert.match(html, /source_marked_review/);
  assert.match(html, /structured_content_available/);
});
