# PDF.js 阅读器 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 知识库论文点击跳转 `/reader/{paperId}` 全屏 PDF 阅读器，pdf.js CDN 渲染，支持文本选择高亮+批注

**Architecture:** 在现有单 HTML SPA 中新增 `pageReader` 页面区域，pdf.js 从 CDN 按需加载，复用已有 annotation API。三栏布局：左侧缩略图栏(骨架)/ 中间 pdf.js canvas / 右侧批注面板(骨架)

**Tech Stack:** Vanilla JS（现有前端风格）, pdf.js 4.x CDN, 复用现有 CSS 变量体系

**Spec:** `docs/superpowers/specs/2026-07-10-pdf-reader-design.md`

## Global Constraints

- pdf.js 从 `cdnjs.cloudflare.com/ajax/libs/pdf.js/4.x/pdf.min.mjs` 加载，worker 同版本
- 批注复用 `GET/POST /knowledge/files/{paperId}/annotations`，不新增 API
- 坐标归一化到 0-1（相对页面宽高）
- 构建输出保持单一 `app.html`
- 新增后端端点：`GET /knowledge/files/{paperId}/pdf-info` 返回 `{pages, file_size, file_name}`
- 左右侧栏为骨架 UI（搭好架子，功能后续迭代）

---

## 文件结构

| 文件 | 职责 | 本次改动 |
|------|------|----------|
| `app/routes/knowledge.py` | 新增 pdf-info 端点 | 新增 ~15 行 |
| `frontend/dist/app.html` | 主应用（源码即 dist） | 新增 reader 页面 HTML + CSS + JS |
| `frontend/src/api/client.ts` | API 客户端 | 新增 3 个方法 |

---

### Task 1: 后端新增 pdf-info 端点

**Files:**
- Modify: `app/routes/knowledge.py` — 在 `get_knowledge_file` 后新增

**Interfaces:**
- Produces: `GET /knowledge/files/{paperId}/pdf-info` → `{paper_id, pages, file_size, file_name}`

- [ ] **Step 1: 添加端点**

在 `get_knowledge_file` 端点（约 line 288）之后添加：

```python
@router.get("/files/{paper_id}/pdf-info")
async def get_pdf_info(
    paper_id: str,
    api_key: str = "",
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key or api_key)
    paper = await _find_user_paper(paper_id, user)
    file_path = paper.get("metadata", {}).get("file_path")
    if not file_path:
        raise HTTPException(status_code=404, detail="file not found for this paper")
    resolved = _resolve_tenant_file(file_path, user)
    pages = 0
    if resolved.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(resolved))
            pages = len(reader.pages)
        except Exception:
            pages = 0
    return {
        "paper_id": paper_id,
        "pages": pages,
        "file_size": resolved.stat().st_size,
        "file_name": resolved.name,
    }
```

- [ ] **Step 2: 验证端点**

```bash
curl -s http://127.0.0.1:8000/knowledge/files/paper:pdf:xxx/pdf-info -H "X-API-Key: demo-key"
```

Expected: `{"paper_id": "...", "pages": N, "file_size": N, "file_name": "..."}`

- [ ] **Step 3: Commit**

```bash
git add app/routes/knowledge.py
git commit -m "feat: add GET /knowledge/files/{paper_id}/pdf-info endpoint

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: API 客户端新增方法

**Files:**
- Modify: `frontend/src/api/client.ts` — 新增 3 个方法

**Interfaces:**
- Produces: `client.getAnnotations(paperId)` → `{paper_id, strokes, notes}`
- Produces: `client.saveAnnotations(paperId, strokes, notes)` → `{saved, ...}`
- Produces: `client.getPdfInfo(paperId)` → `{paper_id, pages, file_size, file_name}`

- [ ] **Step 1: 在 ScholarApiClient 类中添加方法**

```typescript
async getAnnotations(paperId: string): Promise<{ paper_id: string; strokes: unknown[]; notes: string }> {
  const response = await fetch(`${this.baseUrl}/knowledge/files/${encodeURIComponent(paperId)}/annotations`, {
    headers: this.headers(),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

async saveAnnotations(paperId: string, strokes: unknown[], notes: string): Promise<{ saved: boolean; paper_id: string }> {
  const response = await fetch(`${this.baseUrl}/knowledge/files/${encodeURIComponent(paperId)}/annotations`, {
    method: 'POST',
    headers: this.headers(true),
    body: JSON.stringify({ strokes, notes }),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

async getPdfInfo(paperId: string): Promise<{ paper_id: string; pages: number; file_size: number; file_name: string }> {
  const response = await fetch(`${this.baseUrl}/knowledge/files/${encodeURIComponent(paperId)}/pdf-info`, {
    headers: this.headers(),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "feat: add getAnnotations, saveAnnotations, getPdfInfo to API client

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: 阅读器页面 HTML + CSS

**Files:**
- Modify: `frontend/dist/app.html` — 新增 pageReader section + CSS

- [ ] **Step 1: 添加 CSS（在 `</style>` 前插入）**

```css
/* ── Reader full-screen page ── */
.reader-page {
  display: flex; flex-direction: column; height: 100vh; background: var(--bg);
}
.reader-topbar {
  display: flex; align-items: center; gap: 12px; padding: 8px 16px;
  border-bottom: 1px solid var(--border); background: var(--surface);
}
.reader-topbar h2 { flex: 1; font-size: 1rem; margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.reader-topbar button { padding: 6px 12px; font-size: .85rem; }
.reader-body { display: flex; flex: 1; min-height: 0; overflow: hidden; }
.reader-sidebar { width: 200px; border-right: 1px solid var(--border); overflow-y: auto; padding: 12px; background: var(--surface); }
.reader-sidebar.right { width: 280px; border-right: none; border-left: 1px solid var(--border); }
.reader-sidebar.collapsed { display: none; }
.reader-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.reader-toolbar {
  display: flex; align-items: center; gap: 8px; padding: 6px 12px;
  border-bottom: 1px solid var(--border); background: var(--surface);
}
.reader-canvas-wrap { flex: 1; overflow: auto; display: flex; justify-content: center; padding: 16px; background: #525659; }
.reader-canvas-wrap canvas { display: block; margin: 0 auto 8px; box-shadow: 0 2px 8px rgba(0,0,0,.3); }
.page-number { width: 50px; text-align: center; font-size: .85rem; }
.zoom-label { font-size: .85rem; min-width: 45px; text-align: center; }
.annotations-list { display: flex; flex-direction: column; gap: 8px; }
.annotation-item { padding: 8px; border-radius: 4px; background: var(--surface); border: 1px solid var(--border); font-size: .85rem; }
.annotation-item .ann-color { display: inline-block; width: 12px; height: 12px; border-radius: 2px; margin-right: 6px; vertical-align: middle; }
.highlight-tooltip {
  position: absolute; z-index: 100; background: var(--surface); border: 1px solid var(--border);
  border-radius: 6px; padding: 4px; display: flex; gap: 4px; box-shadow: 0 4px 12px rgba(0,0,0,.15);
}
.highlight-tooltip button { padding: 4px 8px; font-size: .8rem; border: none; background: none; cursor: pointer; border-radius: 4px; }
.highlight-tooltip button:hover { background: var(--hover); }
.highlight-tooltip .color-btn { width: 20px; height: 20px; border-radius: 50%; border: 2px solid transparent; }
.highlight-tooltip .color-btn.active { border-color: var(--accent); }
@media (max-width: 900px) {
  .reader-sidebar { width: 160px; }
  .reader-sidebar.right { width: 220px; }
}
```

- [ ] **Step 2: 添加 HTML（在 `<section id="pageKnowledge" class="page">` 之后）**

```html
<section id="pageReader" class="page">
  <div class="reader-page">
    <div class="reader-topbar">
      <button id="readerBackBtn" class="plain" type="button">← 返回知识库</button>
      <h2 id="readerTitle">论文阅读</h2>
      <button id="readerToggleLeft" class="plain" type="button">☰</button>
      <button id="readerToggleRight" class="plain" type="button">☰</button>
    </div>
    <div class="reader-body">
      <aside id="readerLeftSidebar" class="reader-sidebar collapsed">
        <div class="subtle" style="padding:20px;text-align:center">缩略图 / 目录<br><small>后续版本</small></div>
      </aside>
      <div class="reader-main">
        <div class="reader-toolbar">
          <button id="readerPrevPage" class="plain" type="button">◀</button>
          <input id="readerPageInput" class="page-number" type="number" value="1" min="1">
          <span class="subtle">/ <span id="readerTotalPages">1</span></span>
          <span style="flex:1"></span>
          <button id="readerZoomOut" class="plain" type="button">−</button>
          <span id="readerZoomLabel" class="zoom-label">100%</span>
          <button id="readerZoomIn" class="plain" type="button">+</button>
          <button id="readerZoomFit" class="plain" type="button">适应宽度</button>
        </div>
        <div id="readerCanvasWrap" class="reader-canvas-wrap">
          <div class="reader-empty" style="color:#ccc;padding:60px">加载中...</div>
        </div>
      </div>
      <aside id="readerRightSidebar" class="reader-sidebar right collapsed">
        <div class="panel-head" style="margin-bottom:8px"><h3>批注</h3></div>
        <div id="readerAnnotations" class="annotations-list">
          <div class="subtle" style="text-align:center">暂无批注</div>
        </div>
      </aside>
    </div>
  </div>
</section>
```

- [ ] **Step 3: Commit**

```bash
git add frontend/dist/app.html
git commit -m "feat: add reader page HTML + CSS skeleton

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: pdf.js 集成 + 翻页/缩放

**Files:**
- Modify: `frontend/dist/app.html` — 添加 JS 逻辑

- [ ] **Step 1: 在现有 `<script>` 块中添加 pdf.js 加载器**

```javascript
// ── PDF Reader state ──
let pdfDoc = null;
let readerPageNum = 1;
let readerScale = 1.5;
let readerPaperId = null;
let readerAnnotations = [];

// Load pdf.js from CDN on demand
function loadPdfJs() {
  if (window.pdfjsLib) return Promise.resolve(window.pdfjsLib);
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/pdf.min.mjs';
    script.type = 'module';
    script.onload = () => {
      window.pdfjsLib.GlobalWorkerOptions.workerSrc =
        'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/pdf.worker.min.mjs';
      resolve(window.pdfjsLib);
    };
    script.onerror = reject;
    document.head.appendChild(script);
  });
}

async function openReader(paperId) {
  readerPaperId = paperId;
  readerPageNum = 1;
  readerScale = 1.5;

  // Show reader page
  document.querySelectorAll('.page').forEach(p => p.style.display = 'none');
  document.getElementById('pageReader').style.display = '';
  document.getElementById('readerTitle').textContent = '加载中...';
  document.getElementById('readerCanvasWrap').innerHTML =
    '<div class="reader-empty" style="color:#ccc;padding:60px">加载中...</div>';

  try {
    await loadPdfJs();
    const fileUrl = `/knowledge/files/${encodeURIComponent(paperId)}`;
    const loadingTask = window.pdfjsLib.getDocument(fileUrl);
    pdfDoc = await loadingTask.promise;
    document.getElementById('readerTitle').textContent =
      paperId; // Will be updated from metadata
    document.getElementById('readerTotalPages').textContent = pdfDoc.numPages;
    document.getElementById('readerPageInput').max = pdfDoc.numPages;
    await loadAnnotations();
    await renderPage();
  } catch (err) {
    document.getElementById('readerCanvasWrap').innerHTML =
      `<div class="reader-empty" style="color:#c00;padding:60px">加载失败: ${err.message}</div>`;
  }
}

async function renderPage() {
  if (!pdfDoc) return;
  const page = await pdfDoc.getPage(readerPageNum);
  const viewport = page.getViewport({ scale: readerScale });
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  canvas.height = viewport.height;
  canvas.width = viewport.width;
  const wrap = document.getElementById('readerCanvasWrap');
  wrap.innerHTML = '';
  wrap.appendChild(canvas);
  await page.render({ canvasContext: ctx, viewport }).promise;
  document.getElementById('readerPageInput').value = readerPageNum;
  document.getElementById('readerZoomLabel').textContent = Math.round(readerScale * 100) + '%';
}

// ── Page navigation ──
document.getElementById('readerPrevPage').addEventListener('click', async () => {
  if (readerPageNum <= 1) return;
  readerPageNum--;
  await renderPage();
});
document.getElementById('readerNextPage').addEventListener('click', async () => {
  if (!pdfDoc || readerPageNum >= pdfDoc.numPages) return;
  readerPageNum++;
  await renderPage();
});
document.getElementById('readerPageInput').addEventListener('change', async () => {
  const n = parseInt(document.getElementById('readerPageInput').value);
  if (pdfDoc && n >= 1 && n <= pdfDoc.numPages) {
    readerPageNum = n;
    await renderPage();
  }
});

// ── Zoom ──
document.getElementById('readerZoomIn').addEventListener('click', async () => {
  readerScale = Math.min(4, readerScale + 0.25);
  await renderPage();
});
document.getElementById('readerZoomOut').addEventListener('click', async () => {
  readerScale = Math.max(0.5, readerScale - 0.25);
  await renderPage();
});
document.getElementById('readerZoomFit').addEventListener('click', async () => {
  const wrap = document.getElementById('readerCanvasWrap');
  const w = wrap.clientWidth - 32;
  if (pdfDoc) {
    const page = await pdfDoc.getPage(readerPageNum);
    const vp = page.getViewport({ scale: 1 });
    readerScale = w / vp.width;
    await renderPage();
  }
});

// ── Sidebar toggles ──
document.getElementById('readerToggleLeft').addEventListener('click', () => {
  document.getElementById('readerLeftSidebar').classList.toggle('collapsed');
});
document.getElementById('readerToggleRight').addEventListener('click', () => {
  document.getElementById('readerRightSidebar').classList.toggle('collapsed');
});

// ── Back button ──
document.getElementById('readerBackBtn').addEventListener('click', () => {
  document.getElementById('pageReader').style.display = 'none';
  // Navigate back to knowledge page
  window.location.hash = '#/knowledge';
});
```

- [ ] **Step 2: 验证**

手动测试：启动服务 → 上传 PDF → 构建后点击论文应在新页面打开 PDF

- [ ] **Step 3: Commit**

```bash
git add frontend/dist/app.html
git commit -m "feat: integrate pdf.js CDN with page nav and zoom

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: 文本选择高亮 + 批注条

**Files:**
- Modify: `frontend/dist/app.html` — 添加选择监听 + 高亮工具条 + annotation CRUD

- [ ] **Step 1: 添加批注加载/保存逻辑**

```javascript
// ── Annotations ──
async function loadAnnotations() {
  try {
    const resp = await fetch(`/knowledge/files/${encodeURIComponent(readerPaperId)}/annotations`, {
      headers: apiHeaders(),
    });
    if (!resp.ok) { readerAnnotations = []; return; }
    const data = await resp.json();
    readerAnnotations = (data.strokes || []).map(s => ({
      page: s.page || 0,
      type: s.type || 'highlight',
      color: s.color || '#ffeb3b',
      points: s.points || [],
      content: s.content || '',
    }));
    if (data.notes) {
      readerAnnotations.push({
        page: 0, type: 'note', color: null, points: [], content: data.notes,
      });
    }
    renderAnnotationList();
  } catch (e) { readerAnnotations = []; }
}

async function saveAnnotations() {
  const strokes = readerAnnotations
    .filter(a => a.type !== 'note')
    .map(a => ({ page: a.page, type: a.type, color: a.color, points: a.points, content: a.content }));
  const notes = readerAnnotations
    .filter(a => a.type === 'note')
    .map(a => a.content).join('\n');
  await fetch(`/knowledge/files/${encodeURIComponent(readerPaperId)}/annotations`, {
    method: 'POST',
    headers: apiHeaders(true),
    body: JSON.stringify({ strokes, notes }),
  });
}

function renderAnnotationList() {
  const container = document.getElementById('readerAnnotations');
  const items = readerAnnotations.filter(a => a.page === readerPageNum);
  if (!items.length) {
    container.innerHTML = '<div class="subtle" style="text-align:center">本页暂无批注</div>';
    return;
  }
  container.innerHTML = items.map(a => `
    <div class="annotation-item">
      ${a.type !== 'note' ? `<span class="ann-color" style="background:${a.color}"></span>` : ''}
      <span>${a.type === 'note' ? '📝' : '🖍️'} ${a.content || '(无文字)'}</span>
    </div>
  `).join('');
}
```

- [ ] **Step 2: 添加文本选择高亮工具条**

```javascript
// ── Text selection → highlight tooltip ──
let highlightTooltip = null;
const HIGHLIGHT_COLORS = ['#ffeb3b', '#a5d6a7', '#ef9a9a', '#90caf9', '#ce93d8', '#ffcc80'];

function createTooltip() {
  if (highlightTooltip) return;
  highlightTooltip = document.createElement('div');
  highlightTooltip.className = 'highlight-tooltip';
  highlightTooltip.style.display = 'none';
  HIGHLIGHT_COLORS.forEach(color => {
    const btn = document.createElement('button');
    btn.className = 'color-btn';
    btn.style.background = color;
    btn.addEventListener('click', async () => {
      const sel = window.getSelection();
      if (!sel.rangeCount) return;
      // Create highlight annotation at current page
      readerAnnotations.push({
        page: readerPageNum,
        type: 'highlight',
        color: color,
        points: [], // Could capture selection coordinates if needed
        content: sel.toString().substring(0, 200),
      });
      await saveAnnotations();
      renderAnnotationList();
      highlightTooltip.style.display = 'none';
      sel.removeAllRanges();
    });
    highlightTooltip.appendChild(btn);
  });
  // Add note button
  const noteBtn = document.createElement('button');
  noteBtn.textContent = '💬';
  noteBtn.title = '添加批注';
  noteBtn.addEventListener('click', async () => {
    const sel = window.getSelection();
    if (!sel.rangeCount) return;
    const text = sel.toString().substring(0, 200);
    const note = prompt('批注内容:', text);
    if (note) {
      readerAnnotations.push({
        page: readerPageNum, type: 'note', color: null, points: [], content: note,
      });
      await saveAnnotations();
      renderAnnotationList();
    }
    highlightTooltip.style.display = 'none';
    sel.removeAllRanges();
  });
  highlightTooltip.appendChild(noteBtn);
  document.body.appendChild(highlightTooltip);
}

document.addEventListener('mouseup', (e) => {
  const sel = window.getSelection();
  if (!sel.toString().trim() || !readerPaperId) {
    if (highlightTooltip) highlightTooltip.style.display = 'none';
    return;
  }
  createTooltip();
  const rect = sel.getRangeAt(0).getBoundingClientRect();
  highlightTooltip.style.top = (rect.bottom + window.scrollY + 6) + 'px';
  highlightTooltip.style.left = (rect.left + window.scrollX) + 'px';
  highlightTooltip.style.display = 'flex';
});
```

- [ ] **Step 3: 翻页时刷新批注列表**

在 `renderPage()` 末尾添加 `renderAnnotationList();`

- [ ] **Step 4: 手动测试**

启动服务 → 上传 PDF → 打开 reader → 选中文字 → 选颜色 → 批注保存 → 翻页后回来批注还在

- [ ] **Step 5: Commit**

```bash
git add frontend/dist/app.html
git commit -m "feat: text selection highlight + annotation tooltip in PDF reader

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: 路由集成 + 知识库卡片跳转

**Files:**
- Modify: `frontend/dist/app.html` — 修改知识库论文点击行为

- [ ] **Step 1: 处理 URL hash 路由**

在现有路由逻辑中添加 `/reader/{paperId}` 处理：

```javascript
// ── Router: handle /reader/{paperId} ──
function handleRoute() {
  const hash = window.location.hash.replace('#', '') || '/knowledge';
  if (hash.startsWith('/reader/')) {
    const paperId = hash.replace('/reader/', '');
    openReader(paperId);
  } else {
    // ... existing page switching logic
  }
}
window.addEventListener('hashchange', handleRoute);
```

- [ ] **Step 2: 修改知识库论文链接**

找到知识库中论文点击的代码（搜索 `paperPreviewTitle` 相关逻辑），添加"打开阅读器"按钮或修改现有点击行为，使其设置 `window.location.hash = '#/reader/' + paperId`

- [ ] **Step 3: 在 reader topbar 显示论文标题**

修改 `openReader` 中的标题获取：从知识库 API 获取论文 metadata 后显示真实标题

- [ ] **Step 4: Build 并验证端到端**

```bash
cd frontend && npm run build
cd .. && uvicorn app.main:app --reload
# 浏览器打开 → 知识库 → 点击论文 → 应跳转 reader 页面
```

- [ ] **Step 5: Commit**

```bash
git add frontend/dist/app.html
git commit -m "feat: route integration - knowledge paper click opens /reader/{paperId}

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### 最终验证

```bash
cd e:/code/ScholarAgent/ScholarAgent
python -m pytest tests/ -v 2>&1 | tail -5
```

Expected: 全部 20 测试仍通过（前端改动不影响后端测试）
