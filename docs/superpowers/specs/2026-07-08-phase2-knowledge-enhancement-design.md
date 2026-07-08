# Phase 2: 知识库增强 设计文档

> 日期：2026-07-08
> 状态：已确认，待实施
> 前置：Phase 1 全部完成（SQLite、ChromaDB、配置管理）

## 一、范围

聚焦三个核心子任务：

1. **知识库可选加入** — 上传/保存论文时可选择是否索引到 ChromaDB
2. **批注 SQLite 持久化** — 从 JSON 文件迁到 `scholar_annotations` 表
3. **智能分块** — 按段落/句子边界切分，替代固定字符切割

跳过：PDF.js 前端阅读器、本地 ONNX 嵌入模型（后续独立迭代）。

## 二、知识库可选加入

### 2.1 现状

- `scholar_knowledge_papers` 表已有 `in_knowledge_base INTEGER DEFAULT 1` 列
- `KnowledgeStore.save_paper()` 未写入此列，每篇论文都自动索引
- 上传 API 无 `in_knowledge_base` 参数

### 2.2 改动

**API 层（`app/routes/knowledge.py`）：**

| 端点 | 改动 |
|------|------|
| `POST /knowledge/upload` | 新增 Form 参数 `in_knowledge_base: bool = True` |
| `POST /knowledge` | `KnowledgePaperDTO` 新增 `in_knowledge_base: bool = True` |
| `PUT /knowledge/{paper_id}/toggle-kb` | 🆕 切换已存论文的知识库状态 |

**Store 层（`mcp_server/scholar_mcp/store.py`）：**

- `save_paper()` 写入 `file_path` 和 `in_knowledge_base` 列
- `in_knowledge_base=false` 时跳过 `rag_service.index_paper()`
- `save_paper()` 被 toggle 调用时，若从 `false→true` 需重新索引

**MCP Tool（`mcp_server/scholar_mcp/tools.py`）：**

- 🆕 `toggle_knowledge_base` tool — 切换索引状态并触发索引/清理

### 2.3 流程

```
POST /knowledge/upload (in_knowledge_base=true)
  → 存文件 → 提取文本 → 写入 SQLite（in_knowledge_base=1）
  → rag_service.index_paper() → ChromaDB

POST /knowledge/upload (in_knowledge_base=false)
  → 存文件 → 提取文本 → 写入 SQLite（in_knowledge_base=0）
  → 跳过 ChromaDB

PUT /knowledge/{id}/toggle-kb (in_knowledge_base=true)
  → 更新 SQLite → 重新提取全文 → rag_service.index_paper()

PUT /knowledge/{id}/toggle-kb (in_knowledge_base=false)
  → 更新 SQLite → rag_service.delete_paper() → 清理 ChromaDB
```

## 三、批注 SQLite 持久化

### 3.1 现状

- 批注 API 已存在：`GET/POST /knowledge/files/{paper_id}/annotations`
- 数据存为独立 JSON 文件：`storage/annotations/{tenant}/{user}/{digest}.json`
- 格式：`{strokes: [...], notes: ""}`，无页码、无类型区分

### 3.2 新表

```sql
CREATE TABLE scholar_annotations (
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
        ON DELETE CASCADE
);
```

### 3.3 DTO

```python
class AnnotationDTO(BaseModel):
    page: int = 0
    annotation_type: str = "highlight"   # highlight | note | drawing
    color: str | None = None
    points: list[dict[str, Any]] = []    # 坐标数组
    content: str = ""                    # 文字批注
```

### 3.4 API（不变）

```
GET  /knowledge/files/{paper_id}/annotations  → 返回 annotation 列表
POST /knowledge/files/{paper_id}/annotations  → 全量替换（前端发完整列表）
```

与现有 API 签名一致，只改内部实现：原来读写 JSON 文件，改为读写 `scholar_annotations` 表。

### 3.5 迁移

启动时检测 `storage/annotations/` 下有 JSON 文件 → 读取 → 写入新表 → 源文件改 `.json.bak`。

## 四、智能分块

### 4.1 现状

`_chunk_text()` 固定按 `rag_chunk_size`（默认 900 字符）滑动窗口切割，可能切在词中/句中。

### 4.2 新算法

```
输入：全文文本
1. 按双换行（\n\n）切段落
2. 对每个段落：
   - 若 ≤ chunk_size → 作为一个 chunk
   - 若 > chunk_size → 按句末标点（。！？. ! ?）切句子
   - 对每个句子：
     - 若 ≤ chunk_size → 合并到当前 chunk，超出则新起 chunk
     - 若 > chunk_size → 按 chunk_size 字符切割（兜底）
3. 相邻 chunk 之间保留 overlap 字符的重叠
```

### 4.3 配置

```python
# 新增
rag_chunk_strategy: str = "paragraph"  # paragraph | fixed
```

`fixed` 保留旧行为，向后兼容。

## 五、影响范围汇总

| 文件 | 改动 |
|------|------|
| `app/routes/knowledge.py` | `in_knowledge_base` 参数 + `PUT /{id}/toggle-kb` 端点 |
| `app/services/mysql_store.py` | 新增 `scholar_annotations` DDL + annotation CRUD |
| `app/services/rag_service.py` | `_chunk_text` 改为段落感知算法 |
| `app/config.py` | 新增 `rag_chunk_strategy` 配置项 |
| `app/services/runtime_config.py` | 新增 `rag_chunk_strategy` 可配置 key |
| `mcp_server/scholar_mcp/store.py` | `save_paper` 写入 `file_path`/`in_knowledge_base` |
| `mcp_server/scholar_mcp/tools.py` | 🆕 `toggle_knowledge_base` 工具 |

## 六、测试要点

- 上传论文 `in_knowledge_base=false`，验证 ChromaDB 无数据
- 已存论文 toggle 到 `true`，验证 ChromaDB 出现数据
- 已存论文 toggle 到 `false`，验证 ChromaDB 数据被清理
- 批注 JSON → SQLite 自动迁移
- 段落分块不会切在词中
- `fixed` 模式保持旧行为
- 后端 API 兼容（annotation 接口签名不变）
