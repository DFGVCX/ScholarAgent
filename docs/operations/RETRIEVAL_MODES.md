# RAG 检索模式

统一检索请求通过 `retrieval_mode` 选择候选源，默认保持 `hybrid`：

| 模式 | Lexical SQL | Query Embedding / pgvector | 排名 | 失败行为 |
| --- | --- | --- | --- | --- |
| `lexical` | 是 | 否 | 单路顺序 | 不依赖 Embedding |
| `vector` | 否 | 是 | 单路顺序 | 返回明确告警和空结果，不静默调用 lexical |
| `hybrid` | 是 | 是 | RRF | 向量超时或不可用时保留 lexical 结果 |

API 示例：

```text
GET /knowledge/rag/search?query=联邦学习是什么&limit=6&retrieval_mode=vector
```

响应中的 `retrieval_mode` 是实际执行结果；`ranking_policy.requested_mode` 保存请求模式。例如请求 `hybrid` 但 Embedding 不可用时，前者为 `lexical`，后者仍为 `hybrid`。调试信息中的两个候选池会保留计数；未选择的候选源计数为 0，查询向量状态为 `not_requested`。

网站“模型配置 → RAG 检索验证”提供逐查询选择器。当前选择器用于隔离调试，尚不是三种结果的并排评测页；reranker 接入后再完成四策略并排比较。

## 阶段耗时

每次响应的 `debug.timings_ms` 使用单调时钟记录：

- `lexical_sql_ms`：PostgreSQL 词面候选查询；
- `query_embedding_ms`：千问 Query Embedding 请求；
- `vector_sql_ms`：pgvector 候选查询；
- `semantic_total_ms`：Embedding 与向量查询的总耗时，也用于保留超时现场；
- `fusion_context_ms`：RRF/单路排序、去重、多样性和相邻上下文合并；
- `external_search_ms`：启用外部论文源时的查询耗时；
- `total_ms`：检索服务端到端耗时。

没有执行的阶段返回 `null`。向量超时或不可用时，`semantic_total_ms` 仍有值，但无法可靠归因到尚未完成的内部阶段，因此对应的 `query_embedding_ms` / `vector_sql_ms` 保持 `null`，避免伪造精度。
