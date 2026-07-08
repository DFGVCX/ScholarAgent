# Phase 2 知识库增强 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现知识库可选加入、批注 SQLite 持久化、段落感知智能分块

**Architecture:** 三个独立改动的子系统：(1) Store/Tools/Routes 层接通 `in_knowledge_base` 列并新增 toggle API，(2) 新增 `scholar_annotations` 表替代 JSON 文件存储，(3) 重写 `_chunk_text` 支持按段落/句子边界切分

**Tech Stack:** Python 3.12+, FastAPI, SQLite3, ChromaDB, pypdf

**Spec:** `docs/superpowers/specs/2026-07-08-phase2-knowledge-enhancement-design.md`

## Global Constraints

- SQLite 主存储，不能引入新的外部依赖
- 现有 API 签名保持兼容（annotation 端点不改签名）
- `rag_chunk_strategy` 默认值 `"paragraph"`，保留 `"fixed"` 向后兼容
- 批注 JSON→SQLite 迁移在 `initialize_database()` 中自动执行
- 测试用 `unittest.IsolatedAsyncioTestCase` 风格

---

## 文件结构

| 文件 | 职责 | 本次改动 |
|------|------|----------|
| `mcp_server/scholar_mcp/models.py` | PaperRecord 数据类 | 新增 `file_path`, `in_knowledge_base` 字段 |
| `mcp_server/scholar_mcp/store.py` | KnowledgeStore：论文 CRUD | INSERT 包含新列，toggle 方法，skip index |
| `mcp_server/scholar_mcp/tools.py` | MCP 工具注册 | 新增 `toggle_knowledge_base` tool |
| `app/routes/knowledge.py` | REST API | DTO 加字段，upload 加参数，toggle 端点，annotation 改为 SQLite |
| `app/services/mysql_store.py` | SQLite schema + 通用查询 | 新增 `scholar_annotations` DDL + annotation CRUD + migration |
| `app/services/rag_service.py` | RAG 分块/索引/搜索 | `_chunk_text` 改为段落感知算法 |
| `app/config.py` | Settings dataclass | 新增 `rag_chunk_strategy` |
| `app/services/runtime_config.py` | 运行时配置 | 新增 `SCHOLAR_RAG_CHUNK_STRATEGY` key |
| `tests/test_phase2_knowledge.py` | 🆕 测试文件 | 全部新功能的测试 |

---

### Task 1: PaperRecord 模型增加新字段

**Files:**
- Modify: `mcp_server/scholar_mcp/models.py:29-47`

**Interfaces:**
- Produces: `PaperRecord(file_path="", in_knowledge_base=True)` — 两个新字段都有默认值，向后兼容

- [ ] **Step 1: 修改 PaperRecord 数据类**

在 `PaperRecord` 的 `metadata` 字段后添加两个新字段：

```python
@dataclass
class PaperRecord:
    paper_id: str
    tenant_id: str
    user_id: str
    source: str
    title: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    full_text: str = ""
    published_at: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    url: str | None = None
    file_path: str = ""
    in_knowledge_base: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

- [ ] **Step 2: 运行现有测试确认不改坏**

```bash
cd e:/code/ScholarAgent/ScholarAgent && python -m pytest tests/test_auth_routes_and_knowledge.py -v 2>&1 | tail -20
```

Expected: 全部通过（新字段有默认值，不破坏现有调用）

- [ ] **Step 3: Commit**

```bash
git add mcp_server/scholar_mcp/models.py
git commit -m "feat: add file_path and in_knowledge_base fields to PaperRecord

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: KnowledgeStore 接通新字段 + 条件索引

**Files:**
- Modify: `mcp_server/scholar_mcp/store.py:33-78`

**Interfaces:**
- Consumes: `PaperRecord.file_path`, `PaperRecord.in_knowledge_base`
- Produces: `knowledge_store.save_paper()` 写入 `file_path` 和 `in_knowledge_base` 列；`false` 时跳过索引
- Produces: `knowledge_store.toggle_kb(tenant_id, user_id, paper_id, in_knowledge_base)` → `bool`

- [ ] **Step 1: 修改 `save_paper()` 的 INSERT 语句**

将 `store.py:34-68` 的 INSERT 改为包含 `file_path` 和 `in_knowledge_base`：

```python
async def save_paper(self, paper: PaperRecord) -> dict[str, Any]:
    in_kb = 1 if paper.in_knowledge_base else 0
    if mysql_store.is_available():
        mysql_store.execute(
            """
            INSERT INTO scholar_knowledge_papers
                (paper_id, tenant_id, user_id, source, title, authors_json, abstract, full_text,
                 published_at, doi, arxiv_id, url, file_path, in_knowledge_base, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, user_id, paper_id) DO UPDATE SET
                source = excluded.source,
                title = excluded.title,
                authors_json = excluded.authors_json,
                abstract = excluded.abstract,
                full_text = excluded.full_text,
                published_at = excluded.published_at,
                doi = excluded.doi,
                arxiv_id = excluded.arxiv_id,
                url = excluded.url,
                file_path = excluded.file_path,
                in_knowledge_base = excluded.in_knowledge_base,
                metadata_json = excluded.metadata_json,
                updated_at = datetime('now')
            """,
            (
                paper.paper_id,
                paper.tenant_id,
                paper.user_id,
                paper.source,
                paper.title,
                mysql_store.encode_json(paper.authors),
                paper.abstract,
                paper.full_text,
                paper.published_at,
                paper.doi,
                paper.arxiv_id,
                paper.url,
                paper.file_path,
                in_kb,
                mysql_store.encode_json(paper.metadata),
            ),
        )
        data = paper.to_dict()
        if paper.in_knowledge_base:
            await rag_service.index_paper(data)
        return data
    # JSON fallback（不改）
    async with self._lock:
        data = self._read_sync()
        data[self._key(paper.tenant_id, paper.user_id, paper.paper_id)] = paper.to_dict()
        self._write_sync(data)
    result = paper.to_dict()
    if paper.in_knowledge_base:
        await rag_service.index_paper(result)
    return result
```

注意：SQLite 使用 `?` 占位符和 `ON CONFLICT ... DO UPDATE`（不是 MySQL 的 `ON DUPLICATE KEY UPDATE`）。

- [ ] **Step 2: 新增 `toggle_kb()` 方法**

在 `save_paper` 方法后添加：

```python
async def toggle_kb(self, tenant_id: str, user_id: str, paper_id: str,
                    in_knowledge_base: bool) -> bool:
    """Toggle a paper's knowledge-base membership. Returns True if toggled on."""
    if mysql_store.is_available():
        mysql_store.execute(
            "UPDATE scholar_knowledge_papers SET in_knowledge_base = ?, updated_at = datetime('now') "
            "WHERE tenant_id = ? AND user_id = ? AND paper_id = ?",
            (1 if in_knowledge_base else 0, tenant_id, user_id, paper_id),
        )
        if in_knowledge_base:
            row = mysql_store.fetch_one(
                "SELECT * FROM scholar_knowledge_papers "
                "WHERE tenant_id = ? AND user_id = ? AND paper_id = ?",
                (tenant_id, user_id, paper_id),
            )
            if row:
                paper_dict = self._from_mysql_row(row)
                await rag_service.index_paper(paper_dict)
        else:
            await rag_service.delete_paper(tenant_id, user_id, paper_id)
        return in_knowledge_base
    # JSON fallback
    async with self._lock:
        data = self._read_sync()
        key = self._key(tenant_id, user_id, paper_id)
        item = data.get(key)
        if item is None:
            key = next((k for k, v in data.items()
                        if v.get("tenant_id") == tenant_id
                        and v.get("user_id") == user_id
                        and v.get("paper_id") == paper_id), "")
            item = data.get(key)
        if not item:
            return False
        item["in_knowledge_base"] = in_knowledge_base
        self._write_sync(data)
    if in_knowledge_base:
        await rag_service.index_paper(item)
    else:
        await rag_service.delete_paper(tenant_id, user_id, paper_id)
    return in_knowledge_base
```

- [ ] **Step 3: 修改 `delete()` 方法同步更新 `_from_mysql_row`**

`_from_mysql_row` 需要返回 `in_knowledge_base` 和 `file_path` 字段：

```python
def _from_mysql_row(self, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "paper_id": row["paper_id"],
        "tenant_id": row["tenant_id"],
        "user_id": row["user_id"],
        "source": row["source"],
        "title": row["title"],
        "authors": mysql_store.decode_json(row.get("authors_json"), []),
        "abstract": row.get("abstract") or "",
        "full_text": row.get("full_text") or "",
        "published_at": row.get("published_at"),
        "doi": row.get("doi"),
        "arxiv_id": row.get("arxiv_id"),
        "url": row.get("url"),
        "file_path": row.get("file_path") or "",
        "in_knowledge_base": bool(row.get("in_knowledge_base", 1)),
        "metadata": mysql_store.decode_json(row.get("metadata_json"), {}),
    }
```

- [ ] **Step 4: 运行测试**

```bash
cd e:/code/ScholarAgent/ScholarAgent && python -m pytest tests/test_auth_routes_and_knowledge.py -v 2>&1 | tail -20
```

Expected: 全部通过

- [ ] **Step 5: Commit**

```bash
git add mcp_server/scholar_mcp/store.py
git commit -m "feat: wire up in_knowledge_base and file_path in KnowledgeStore

- save_paper() writes file_path and in_knowledge_base columns
- in_knowledge_base=false skips ChromaDB indexing
- New toggle_kb() method for toggling paper index status

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: 新增 toggle_knowledge_base MCP tool

**Files:**
- Modify: `mcp_server/scholar_mcp/tools.py:280-288` (insert after `delete_knowledge`)
- Create: `tests/test_phase2_knowledge.py`

**Interfaces:**
- Consumes: `knowledge_store.toggle_kb()`
- Produces: MCP tool `toggle_knowledge_base(tenant_id, user_id, paper_id, in_knowledge_base) → {paper_id, in_knowledge_base}`

- [ ] **Step 1: 写测试**

```python
# tests/test_phase2_knowledge.py
from __future__ import annotations

import unittest
from uuid import uuid4

from app.routes.knowledge import KnowledgePaperDTO, save_knowledge
from app.services.rag_service import rag_service


class KnowledgeBaseToggleTest(unittest.IsolatedAsyncioTestCase):
    async def test_upload_without_indexing_and_toggle_on(self):
        title = f"KB opt-out test {uuid4()}"
        # 1. 保存论文但不索引
        create = await save_knowledge(
            KnowledgePaperDTO(
                source="manual", title=title, authors=["Test"],
                abstract="Testing in_knowledge_base flag.",
                in_knowledge_base=False,
            ),
            x_api_key="demo-key",
        )
        paper_id = create["item"]["paper_id"]
        self.assertFalse(create["item"].get("in_knowledge_base", True))

        # 2. 验证 ChromaDB 中没有
        rag_result = await rag_service.search("tenant_demo", "user_demo", title, 5)
        self.assertFalse(any(item["paper_id"] == paper_id for item in rag_result["items"]))

        # 3. Toggle on（直接调 MCP tool）
        from mcp_server.scholar_mcp.tools import toggle_knowledge_base
        result = await toggle_knowledge_base(
            tenant_id="tenant_demo", user_id="user_demo",
            paper_id=paper_id, in_knowledge_base=True,
        )
        self.assertTrue(result["in_knowledge_base"])

        # 4. 验证 ChromaDB 中现在有了
        rag_result2 = await rag_service.search("tenant_demo", "user_demo", title, 5)
        self.assertTrue(any(item["paper_id"] == paper_id for item in rag_result2["items"]))

    async def test_toggle_off_removes_from_index(self):
        title = f"KB toggle-off test {uuid4()}"
        # 1. 默认索引
        create = await save_knowledge(
            KnowledgePaperDTO(source="manual", title=title, authors=["Test"],
                              abstract="Will be toggled off."),
            x_api_key="demo-key",
        )
        paper_id = create["item"]["paper_id"]

        # 2. 确认在索引中
        rag_result = await rag_service.search("tenant_demo", "user_demo", title, 5)
        self.assertTrue(any(item["paper_id"] == paper_id for item in rag_result["items"]))

        # 3. Toggle off
        from mcp_server.scholar_mcp.tools import toggle_knowledge_base
        result = await toggle_knowledge_base(
            tenant_id="tenant_demo", user_id="user_demo",
            paper_id=paper_id, in_knowledge_base=False,
        )
        self.assertFalse(result["in_knowledge_base"])

        # 4. 确认从索引中移除
        rag_result2 = await rag_service.search("tenant_demo", "user_demo", title, 5)
        self.assertFalse(any(item["paper_id"] == paper_id for item in rag_result2["items"]))
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd e:/code/ScholarAgent/ScholarAgent && python -m pytest tests/test_phase2_knowledge.py -v 2>&1 | tail -20
```

Expected: FAIL — `toggle_knowledge_base` 未定义 或 `KnowledgePaperDTO` 无 `in_knowledge_base` 参数

- [ ] **Step 3: 新增 MCP tool**

在 `mcp_server/scholar_mcp/tools.py` 的 `delete_knowledge` 函数后添加：

```python
@scholar_tool(
    name="toggle_knowledge_base",
    description="Toggle a paper's membership in the vector knowledge base",
    category="knowledge",
    safety_level=SafetyLevel.MEDIUM,
)
async def toggle_knowledge_base(
    tenant_id: str,
    user_id: str,
    paper_id: str,
    in_knowledge_base: bool = True,
) -> dict[str, Any]:
    result = await knowledge_store.toggle_kb(tenant_id, user_id, paper_id, in_knowledge_base)
    return {"paper_id": paper_id, "in_knowledge_base": result}
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd e:/code/ScholarAgent/ScholarAgent && python -m pytest tests/test_phase2_knowledge.py -v 2>&1 | tail -20
```

Expected: 2 passed（或失败因为 `KnowledgePaperDTO` 还缺字段——那在 Task 4 修）

- [ ] **Step 5: Commit**

```bash
git add mcp_server/scholar_mcp/tools.py tests/test_phase2_knowledge.py
git commit -m "feat: add toggle_knowledge_base MCP tool

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: API 层增加 in_knowledge_base 参数 + toggle 端点

**Files:**
- Modify: `app/routes/knowledge.py:30-41` (DTO), `:215-267` (upload endpoint), `:194-212` (save endpoint)
- Insert: 在 `delete_knowledge` 后新增 toggle 端点

**Interfaces:**
- Consumes: `KnowledgeStore.toggle_kb()`
- Produces: `PUT /knowledge/{paper_id}/toggle-kb` 端点

- [ ] **Step 1: 修改 KnowledgePaperDTO**

```python
class KnowledgePaperDTO(BaseModel):
    paper_id: str | None = Field(default=None, max_length=260)
    source: str = Field(default="manual", max_length=40)
    title: str = Field(..., min_length=1, max_length=500)
    authors: list[str] = Field(default_factory=list)
    abstract: str = Field(default="", max_length=8000)
    full_text: str = Field(default="", max_length=50000)
    published_at: str | None = Field(default=None, max_length=40)
    doi: str | None = Field(default=None, max_length=200)
    arxiv_id: str | None = Field(default=None, max_length=120)
    url: str | None = Field(default=None, max_length=500)
    in_knowledge_base: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 2: 修改 upload 端点，增加 Form 参数**

在 `upload_knowledge_file` 函数签名中（约 line 215），添加参数：

```python
@router.post("/upload")
async def upload_knowledge_file(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    authors: str = Form(default=""),
    source: str = Form(default="pdf"),
    published_at: str = Form(default=""),
    doi: str = Form(default=""),
    arxiv_id: str = Form(default=""),
    url: str = Form(default=""),
    abstract: str = Form(default=""),
    in_knowledge_base: bool = Form(default=True),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
```

并在构建 `KnowledgePaperDTO` 时传入（约 line 247）：

```python
    paper = KnowledgePaperDTO(
        paper_id=paper_id,
        source=normalized_source,
        title=paper_title,
        authors=[item.strip() for item in authors.split(",") if item.strip()],
        abstract=abstract.strip() or full_text[:900],
        full_text=full_text,
        published_at=published_at.strip() or None,
        doi=doi.strip() or None,
        arxiv_id=arxiv_id.strip() or None,
        url=url.strip() or None,
        in_knowledge_base=in_knowledge_base,
        metadata={
            "created_from": "web_upload",
            "file_name": safe_name,
            "file_path": str(stored_path),
            "file_url": f"/knowledge/files/{paper_id}",
            "content_type": file.content_type or "application/octet-stream",
            "content_length": len(raw),
        },
    )
```

- [ ] **Step 3: 新增 toggle 端点**

在 `delete_knowledge` 端点后（约 line 385）添加：

```python
class ToggleKbDTO(BaseModel):
    in_knowledge_base: bool


@router.put("/{paper_id}/toggle-kb")
async def toggle_knowledge_base(
    paper_id: str,
    request: ToggleKbDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    await _find_user_paper(paper_id, user)
    from mcp_server.scholar_mcp.tools import toggle_knowledge_base as _toggle_kb
    result = await _toggle_kb(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        paper_id=paper_id,
        in_knowledge_base=request.in_knowledge_base,
    )
    stats = await rag_service.stats(user.tenant_id, user.user_id)
    return {**result, "rag": stats}
```

- [ ] **Step 4: 运行测试**

```bash
cd e:/code/ScholarAgent/ScholarAgent && python -m pytest tests/test_phase2_knowledge.py -v 2>&1 | tail -20
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/routes/knowledge.py
git commit -m "feat: add in_knowledge_base param to upload/save + toggle-kb endpoint

- KnowledgePaperDTO gets in_knowledge_base field (default True)
- POST /knowledge/upload gets in_knowledge_base form param
- New PUT /knowledge/{paper_id}/toggle-kb endpoint

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: 新增 scholar_annotations 表 + CRUD

**Files:**
- Modify: `app/services/mysql_store.py` — SCHEMA_SQL, 新增 annotation 函数

**Interfaces:**
- Produces: `mysql_store.save_annotations(tenant_id, user_id, paper_id, annotations: list[dict]) → int`
- Produces: `mysql_store.get_annotations(tenant_id, user_id, paper_id) → list[dict]`
- Produces: `mysql_store.migrate_annotations_json() → int` (迁移旧 JSON 文件)

- [ ] **Step 1: 添加 DDL 到 SCHEMA_SQL**

在 `mysql_store.py` 的 `SCHEMA_SQL` tuple 中（`scholar_trace_events` 之后，约 line 278），添加：

```python
    """CREATE TABLE IF NOT EXISTS scholar_annotations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        page INTEGER NOT NULL DEFAULT 0,
        annotation_type TEXT NOT NULL DEFAULT 'highlight',
        color TEXT,
        points_json TEXT,
        content TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (tenant_id, user_id, paper_id)
            REFERENCES scholar_knowledge_papers(tenant_id, user_id, paper_id)
            ON DELETE CASCADE)""",
```

- [ ] **Step 2: 添加 annotation CRUD 函数**

在 `get_all_settings()` 函数后（约 line 373），添加：

```python
# ---------------------------------------------------------------------------
# Annotation CRUD
# ---------------------------------------------------------------------------

def save_annotations(tenant_id: str, user_id: str, paper_id: str,
                     annotations: list[dict[str, Any]]) -> int:
    """Replace all annotations for a paper. Returns count saved."""
    execute("DELETE FROM scholar_annotations WHERE tenant_id = ? AND user_id = ? AND paper_id = ?",
            (tenant_id, user_id, paper_id))
    count = 0
    for ann in annotations:
        execute(
            "INSERT INTO scholar_annotations "
            "(paper_id, tenant_id, user_id, page, annotation_type, color, points_json, content) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                paper_id, tenant_id, user_id,
                int(ann.get("page", 0)),
                str(ann.get("annotation_type", "highlight")),
                ann.get("color"),
                encode_json(ann.get("points", [])),
                str(ann.get("content", "")),
            ),
        )
        count += 1
    return count


def get_annotations(tenant_id: str, user_id: str, paper_id: str) -> list[dict[str, Any]]:
    """Get all annotations for a paper."""
    rows = fetch_all(
        "SELECT id, page, annotation_type, color, points_json, content, created_at, updated_at "
        "FROM scholar_annotations "
        "WHERE tenant_id = ? AND user_id = ? AND paper_id = ? "
        "ORDER BY page, id",
        (tenant_id, user_id, paper_id),
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append({
            "id": row["id"],
            "page": row["page"],
            "annotation_type": row["annotation_type"],
            "color": row.get("color"),
            "points": decode_json(row.get("points_json"), []),
            "content": row.get("content") or "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })
    return result


def migrate_annotations_json() -> int:
    """Migrate legacy JSON annotation files to SQLite. Returns count of migrated papers."""
    import os as _os
    from pathlib import Path as _Path
    annotations_root = _Path(_os.getenv("SCHOLAR_STORAGE_DIR", "storage/runtime")) / "annotations"
    if not annotations_root.exists():
        return 0
    count = 0
    for json_file in annotations_root.rglob("*.json"):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        paper_id = data.get("paper_id", "")
        if not paper_id:
            continue
        # Read tenant_id/user_id from directory structure: annotations/{tenant}/{user}/{digest}.json
        parts = json_file.relative_to(annotations_root).parts
        if len(parts) < 2:
            continue
        tenant_id, user_id = parts[0], parts[1]
        strokes = data.get("strokes", [])
        notes = data.get("notes", "")
        # Convert old format to new: each stroke becomes an annotation row
        annotations: list[dict[str, Any]] = []
        for stroke in strokes:
            annotations.append({
                "page": stroke.get("page", 0),
                "annotation_type": stroke.get("type", "highlight"),
                "color": stroke.get("color"),
                "points": stroke.get("points", []),
                "content": "",
            })
        if notes:
            annotations.append({
                "page": 0,
                "annotation_type": "note",
                "color": None,
                "points": [],
                "content": notes,
            })
        if annotations:
            save_annotations(tenant_id, user_id, paper_id, annotations)
            count += 1
        # Rename migrated file
        try:
            json_file.rename(json_file.with_suffix(".json.bak"))
        except OSError:
            pass
    return count
```

- [ ] **Step 3: 在 `initialize_database()` 中调用迁移**

在 `initialize_database()` 函数（约 line 301）的 `seed_demo_data()` 调用后添加：

```python
def initialize_database(create_database: bool = True) -> dict[str, Any]:
    conn = _get_conn()
    for statement in SCHEMA_SQL:
        conn.execute(statement)
    for index_sql in _INDEXES_SQL:
        conn.execute(index_sql)
    seed_demo_data()
    migrated = migrate_annotations_json()
    result = {"database": "scholar_agent", "tables": len(SCHEMA_SQL)}
    if migrated:
        result["migrated_annotations"] = migrated
    return result
```

- [ ] **Step 4: 验证表创建成功**

```bash
cd e:/code/ScholarAgent/ScholarAgent && python -c "
from app.services import mysql_store
mysql_store.initialize_database()
row = mysql_store.fetch_one(\"SELECT COUNT(*) as cnt FROM sqlite_master WHERE type='table' AND name='scholar_annotations'\")
print('Table exists:', row['cnt'] == 1)
"
```

Expected: `Table exists: True`

- [ ] **Step 5: Commit**

```bash
git add app/services/mysql_store.py
git commit -m "feat: add scholar_annotations table with CRUD and JSON migration

- New scholar_annotations table (page, type, color, points, content)
- save_annotations() / get_annotations() CRUD functions
- migrate_annotations_json() for legacy JSON file migration
- Called automatically in initialize_database()

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: 重写 annotation 端点改用 SQLite

**Files:**
- Modify: `app/routes/knowledge.py:290-320`（`get_file_annotations` 和 `save_file_annotations`）
- Modify: `app/routes/knowledge.py:135-139`（`_annotation_path` 函数可移除）

**Interfaces:**
- Consumes: `mysql_store.get_annotations()`, `mysql_store.save_annotations()`
- API 签名不变

- [ ] **Step 1: 重写 `get_file_annotations`（保持旧 API 返回格式兼容）**

将原基于 JSON 文件的实现替换为：

```python
@router.get("/files/{paper_id}/annotations")
async def get_file_annotations(
    paper_id: str,
    api_key: str = "",
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key or api_key)
    await _find_user_paper(paper_id, user)
    annotations = mysql_store.get_annotations(user.tenant_id, user.user_id, paper_id)
    # Convert back to old API format for backward compatibility
    strokes: list[dict[str, Any]] = []
    notes_parts: list[str] = []
    for ann in annotations:
        if ann["annotation_type"] == "note":
            if ann["content"]:
                notes_parts.append(ann["content"])
        else:
            strokes.append({
                "page": ann["page"],
                "type": ann["annotation_type"],
                "color": ann.get("color"),
                "points": ann.get("points", []),
                "content": ann.get("content", ""),
            })
    return {
        "paper_id": paper_id,
        "strokes": strokes,
        "notes": "\n".join(notes_parts),
    }
```

- [ ] **Step 2: 重写 `save_file_annotations`（接受旧格式，内部转新表）**

```python
@router.post("/files/{paper_id}/annotations")
async def save_file_annotations(
    paper_id: str,
    request: FileAnnotationDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    await _find_user_paper(paper_id, user)
    # Convert old DTO format to annotation rows
    annotations: list[dict[str, Any]] = []
    for stroke in request.strokes[:1000]:
        annotations.append({
            "page": stroke.get("page", 0),
            "annotation_type": stroke.get("type", "highlight"),
            "color": stroke.get("color"),
            "points": stroke.get("points", []),
            "content": stroke.get("content", ""),
        })
    if request.notes:
        annotations.append({
            "page": 0,
            "annotation_type": "note",
            "color": None,
            "points": [],
            "content": request.notes,
        })
    count = mysql_store.save_annotations(user.tenant_id, user.user_id, paper_id, annotations)
    return {
        "saved": True,
        "paper_id": paper_id,
        "count": count,
        "strokes": request.strokes,
        "notes": request.notes,
    }
```

- [ ] **Step 3: 清理不再需要的 `_annotation_path` 函数**

删除 `knowledge.py` 中 lines 135-139 的 `_annotation_path` 函数（不再被引用）。

- [ ] **Step 4: 在 `FileAnnotationDTO` 中兼容新旧格式**

保持 `FileAnnotationDTO` 不变（`strokes` + `notes`），新增 `AnnotationItemDTO` 用于内部转换：

```python
class FileAnnotationDTO(BaseModel):
    strokes: list[dict[str, Any]] = Field(default_factory=list)
    notes: str = Field(default="", max_length=50000)
```

- [ ] **Step 5: 运行测试**

```bash
cd e:/code/ScholarAgent/ScholarAgent && python -m pytest tests/test_auth_routes_and_knowledge.py -v 2>&1 | tail -20
```

Expected: 全部通过（annotation 相关测试需要适配新返回格式）

- [ ] **Step 6: Commit**

```bash
git add app/routes/knowledge.py
git commit -m "feat: rewrite annotation endpoints to use SQLite storage

- get_file_annotations reads from scholar_annotations table
- save_file_annotations writes to scholar_annotations table
- Remove _annotation_path JSON file helper
- API signatures unchanged

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: 段落感知智能分块

**Files:**
- Modify: `app/services/rag_service.py:23-37`（`_chunk_text` 函数）
- Modify: `app/services/rag_service.py:96-127`（`build_chunks` 传递 strategy）

**Interfaces:**
- Consumes: `get_settings().rag_chunk_strategy`
- Produces: `_chunk_text(text, size, overlap)` — 行为由 `rag_chunk_strategy` 控制

- [ ] **Step 1: 写测试**

在 `tests/test_phase2_knowledge.py` 中添加分块测试：

```python
class ChunkingTest(unittest.TestCase):
    def test_paragraph_chunking_preserves_boundaries(self):
        from app.services.rag_service import _chunk_text
        text = "第一段内容。\n\n第二段内容。\n\n第三段很长" + "的内容。" * 100
        # 用 paragraph 策略，chunk_size=50
        import os
        os.environ["SCHOLAR_RAG_CHUNK_STRATEGY"] = "paragraph"
        os.environ["SCHOLAR_RAG_CHUNK_SIZE"] = "50"
        os.environ["SCHOLAR_RAG_CHUNK_OVERLAP"] = "10"
        chunks = _chunk_text(text, size=50, overlap=10)
        # 段落边界不应被切断："第一段内容。" 和 "第二段内容。" 应各在一个 chunk 中
        self.assertTrue(any("第一段内容" in c for c in chunks))
        self.assertTrue(any("第二段内容" in c for c in chunks))
        # 不应该有跨段落边界的 chunk 同时包含两段开头
        for chunk in chunks:
            self.assertFalse("第一段内容" in chunk and "第二段内容" in chunk)

    def test_fixed_mode_unchanged(self):
        from app.services.rag_service import _chunk_text
        text = "A" * 100 + " " + "B" * 100
        import os
        os.environ["SCHOLAR_RAG_CHUNK_STRATEGY"] = "fixed"
        os.environ["SCHOLAR_RAG_CHUNK_SIZE"] = "30"
        os.environ["SCHOLAR_RAG_CHUNK_OVERLAP"] = "5"
        chunks = _chunk_text(text, size=30, overlap=5)
        self.assertGreater(len(chunks), 1)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd e:/code/ScholarAgent/ScholarAgent && python -m pytest tests/test_phase2_knowledge.py::ChunkingTest -v 2>&1 | tail -15
```

Expected: FAIL — paragraph 模式未实现

- [ ] **Step 3: 重写 `_chunk_text`**

```python
_SENTENCE_END = re.compile(r"[。！？.!?]")

def _chunk_text(text: str, size: int | None = None, overlap: int | None = None) -> list[str]:
    settings = get_settings()
    size = max(200, int(size or settings.rag_chunk_size))
    overlap = min(max(0, int(overlap if overlap is not None else settings.rag_chunk_overlap)), size - 1)
    text = text or ""
    if not text.strip():
        return []
    strategy = settings.rag_chunk_strategy
    if strategy == "fixed":
        return _chunk_fixed(text, size, overlap)
    return _chunk_by_paragraph(text, size, overlap)


def _chunk_fixed(text: str, size: int, overlap: int) -> list[str]:
    """Original fixed-size sliding window chunking."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks: list[str] = []
    cursor = 0
    while cursor < len(text):
        chunk = text[cursor : cursor + size].strip()
        if chunk:
            chunks.append(chunk)
        cursor += max(size - overlap, 1)
    return chunks


def _chunk_by_paragraph(text: str, size: int, overlap: int) -> list[str]:
    """Paragraph-aware chunking: split by paragraphs, then sentences, then fixed."""
    # Step 1: Split by double-newline (paragraphs)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return [text.strip()[:size]]

    chunks: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= size:
            _append_chunk(chunks, paragraph, size, overlap)
        else:
            # Step 2: Split by sentence-ending punctuation
            sentences = _SENTENCE_END.split(paragraph)
            current = ""
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                if len(current) + len(sentence) + 1 <= size:
                    current = (current + " " + sentence).strip() if current else sentence
                else:
                    if len(sentence) > size:
                        # Step 3: Fixed-size fallback for very long sentences
                        if current:
                            _append_chunk(chunks, current, size, overlap)
                            current = ""
                        for i in range(0, len(sentence), max(size - overlap, 1)):
                            sub = sentence[i : i + size].strip()
                            if sub:
                                _append_chunk(chunks, sub, size, overlap)
                    else:
                        if current:
                            _append_chunk(chunks, current, size, overlap)
                        current = sentence
            if current:
                _append_chunk(chunks, current, size, overlap)
    return [c for c in chunks if c]


def _append_chunk(chunks: list[str], text: str, size: int, overlap: int) -> None:
    """Append text, adding overlap from previous chunk end if available."""
    if not chunks:
        chunks.append(text)
        return
    if overlap > 0 and len(chunks[-1]) > overlap:
        prefix = chunks[-1][-overlap:]
        chunks.append(prefix + " " + text)
    else:
        chunks.append(text)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd e:/code/ScholarAgent/ScholarAgent && python -m pytest tests/test_phase2_knowledge.py::ChunkingTest -v 2>&1 | tail -15
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/rag_service.py tests/test_phase2_knowledge.py
git commit -m "feat: implement paragraph-aware chunking strategy

- New _chunk_by_paragraph(): split by paragraphs, then sentences, then fixed
- _chunk_fixed() preserves original behavior
- Controlled by rag_chunk_strategy setting (paragraph|fixed)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: rag_chunk_strategy 配置项

**Files:**
- Modify: `app/config.py:35`（Settings dataclass 新增字段）
- Modify: `app/services/runtime_config.py:9-33`（CONFIG_KEYS, SELECT_OPTIONS, DEFAULT_VALUES）

**Interfaces:**
- Produces: `Settings.rag_chunk_strategy: str = "paragraph"`

- [ ] **Step 1: 添加 Settings 字段**

在 `app/config.py` 的 `Settings` dataclass 中，`rag_chunk_overlap` 后添加：

```python
    rag_chunk_strategy: str = "paragraph"
```

并在 `get_settings()` 工厂函数中添加：

```python
        rag_chunk_strategy=_setting_value(overrides, "SCHOLAR_RAG_CHUNK_STRATEGY", "paragraph").strip().lower(),
```

- [ ] **Step 2: 添加 runtime_config 条目**

在 `runtime_config.py` 中：

`CONFIG_KEYS` tuple 添加 `"SCHOLAR_RAG_CHUNK_STRATEGY"`（在 `SCHOLAR_RAG_CHUNK_OVERLAP` 后）

`SELECT_OPTIONS` dict 添加：

```python
    "SCHOLAR_RAG_CHUNK_STRATEGY": ("paragraph", "fixed"),
```

`DEFAULT_VALUES` dict 添加：

```python
    "SCHOLAR_RAG_CHUNK_STRATEGY": "paragraph",
```

- [ ] **Step 3: 验证**

```bash
cd e:/code/ScholarAgent/ScholarAgent && python -c "
from app.config import get_settings
s = get_settings()
print('rag_chunk_strategy:', s.rag_chunk_strategy)
print('OK')
"
```

Expected: `rag_chunk_strategy: paragraph`

- [ ] **Step 4: Commit**

```bash
git add app/config.py app/services/runtime_config.py
git commit -m "feat: add rag_chunk_strategy config option (paragraph|fixed)

- Settings.rag_chunk_strategy default 'paragraph'
- Runtime config key SCHOLAR_RAG_CHUNK_STRATEGY
- Frontend-configurable via PUT /settings/runtime

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### 最终验证

```bash
cd e:/code/ScholarAgent/ScholarAgent && python -m pytest tests/ -v 2>&1 | tail -30
```

Expected: 全部通过（包括新的 test_phase2_knowledge.py）