(function attachScholarChunkView(global) {
    const TYPE_LABELS = {
        prose: '正文',
        equation: '公式',
        table: '表格',
        figure: '图片',
        algorithm: '算法',
        code: '代码',
    };

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function typeLabel(type) {
        return TYPE_LABELS[type] || '正文';
    }

    function pageLabel(chunk) {
        const start = Number(chunk.page_start);
        const end = Number(chunk.page_end);
        if (!Number.isFinite(start) || start <= 0) return '页码未标注';
        if (Number.isFinite(end) && end > 0 && end !== start) return `第 ${start}–${end} 页`;
        return `第 ${start} 页`;
    }

    function renderFilters(chunks, activeFilter) {
        const types = ['prose', 'equation', 'table', 'figure', 'algorithm', 'code'];
        const button = (type, label, count) => `
            <button class="plain ${activeFilter === type ? 'active' : ''}" type="button" data-chunk-filter="${type}">
                ${label} ${count}
            </button>`;
        return `
            <div class="paper-chunk-filters">
                ${button('all', '全部', chunks.length)}
                ${types.map((type) => button(type, typeLabel(type), chunks.filter((chunk) => chunk.type === type).length)).join('')}
            </div>`;
    }

    function renderCard(chunk) {
        const content = String(chunk.content ?? '');
        const blockCount = Array.isArray(chunk.source_block_ids) ? chunk.source_block_ids.length : 0;
        const status = chunk.embedding_status || 'pending';
        const characterCount = Number.isFinite(Number(chunk.character_count))
            ? Number(chunk.character_count)
            : content.length;
        return `
            <article class="paper-chunk-card" data-chunk-index="${Number(chunk.index || 0)}" data-chunk-type="${escapeHtml(chunk.type || 'prose')}">
                <header class="paper-chunk-head">
                    <div>
                        <strong>Chunk #${Number(chunk.index || 0)}</strong>
                        <span class="paper-chunk-type">${escapeHtml(typeLabel(chunk.type))}</span>
                    </div>
                    <em class="badge ${status === 'ready' ? 'ok' : status === 'failed' ? 'bad' : 'warn'}">${escapeHtml(status)}</em>
                </header>
                <div class="paper-chunk-provenance">
                    <strong>${escapeHtml(chunk.section_path || chunk.section_id || '章节未标注')}</strong>
                    <span>${escapeHtml(pageLabel(chunk))}</span>
                </div>
                <pre class="paper-chunk-source">${escapeHtml(content)}</pre>
                <footer class="paper-chunk-foot">
                    <span>${characterCount} 字符</span>
                    <span>${Number(chunk.token_count || 0)} tokens</span>
                    <span>来源块 ${blockCount}</span>
                    <span title="${escapeHtml(chunk.id || '')}">${escapeHtml(chunk.id || '-')}</span>
                </footer>
            </article>`;
    }

    function render(structure, activeFilter = 'all') {
        const chunks = Array.isArray(structure?.chunks) ? structure.chunks : [];
        const visible = activeFilter === 'all'
            ? chunks
            : chunks.filter((chunk) => chunk.type === activeFilter);
        const chunker = structure?.chunker || {};
        const summary = `
            <div class="paper-chunk-summary">
                <div><strong>${chunks.length} 个切片</strong><span>当前内容版本 v${Number(structure?.content_version || 0)}</span></div>
                <div><span>策略</span><code>${escapeHtml(chunker.strategy || '未标注')}</code><span>版本 ${escapeHtml(chunker.version || '-')}</span></div>
            </div>`;
        const cards = visible.length
            ? `<div class="paper-chunk-list">${visible.map(renderCard).join('')}</div>`
            : '<div class="paper-chunk-list"><div class="alert">当前类型没有切片。</div></div>';
        return `<div class="paper-chunk-view">${summary}${renderFilters(chunks, activeFilter)}${cards}</div>`;
    }

    global.ScholarChunkView = { render, typeLabel };
})(window);
