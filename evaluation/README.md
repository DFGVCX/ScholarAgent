# ScholarAgent 检索评测集

本目录用于比较相同论文、相同查询、相同千问 Embedding 下的四种 PDF 解析与切片策略：

- `legacy_fixed`
- `structure_aware_v1`
- `formula_aware_v2`
- `multimodal_aware_v3`

## 文件

- `corpus.jsonl`：论文身份、相对路径、页数和 SHA-256。
- `queries.jsonl`：28 条中英文查询，以及独立于切片策略的证据标注。
- `corpus/pdfs/`：本地 PDF，已被 Git 忽略，不提交论文原文件。
- `reports/`：实际运行生成的完整排名、指标和汇总报告。

每个证据标注包含 `paper_id`、`page_ranges`、`evidence_terms` 和人工核对的 `evidence_quote`。计算指标时，`evidence_terms` 用于跨策略匹配；页码和引文用于人工审计。这样不会把某种策略切出的 chunk ID 反过来当作该策略自己的标准答案。

## 运行

```powershell
$env:SCHOLAR_RAG_EMBEDDING_API_KEY=$env:DASHSCOPE_API_KEY
python scripts/compare_chunk_strategies.py `
  --corpus-jsonl evaluation/corpus.jsonl `
  --queries-jsonl evaluation/queries.jsonl `
  --output evaluation/reports/chunk-comparison.json `
  --top-k 10
```

## 生产检索链路评测

下面的命令不再离线模拟排名，而是逐条调用网站实际使用的 PostgreSQL 检索链路，
在同一请求中比较 Lexical、Vector、RRF Hybrid 和 Hybrid + Reranker。JSON 报告包含：

- Recall@K、Precision@K、MRR、NDCG@K；
- 中文/英文及查询类别分组指标；
- 查询失败分类、策略降级次数、平均/P95 延迟和上下文 Token 估算；
- 语料、查询、策略、候选池和最终结果指纹。

```powershell
$env:SCHOLAR_DATABASE_URL="postgresql+psycopg://用户:密码@127.0.0.1:5432/scholar_agent"
$env:SCHOLAR_RAG_EMBEDDING_API_KEY=$env:DASHSCOPE_API_KEY
$env:SCHOLAR_RAG_RERANKER_API_KEY=$env:DASHSCOPE_API_KEY
python scripts/evaluate_production_retrieval.py `
  --tenant-id tenant_demo `
  --user-id user_demo `
  --queries-jsonl evaluation/queries.jsonl `
  --output evaluation/reports/production-retrieval.json `
  --top-k 10 `
  --probe-k 50
```

报告必须通过发布门禁后才能作为新基线。门禁会检查四种策略是否齐全、是否发生静默降级、
数据库向量一致性、最低指标和相对基线回退幅度：

```powershell
python scripts/check_rag_release_gate.py `
  --report evaluation/reports/production-retrieval.json `
  --baseline evaluation/baselines/production-retrieval.json
```

没有真实 API Key、固定 PDF 语料或人工证据标签时，不生成或提交伪造的生产指标。

## HNSW 压测

HNSW 压测直接对生产 SQL 执行 `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`，比较不同
`hnsw.ef_search` 下的 P50/P95/P99、索引命中率和缓冲区读写。默认要求至少 1000 个当前
版本 ready 向量，避免把小样本 smoke 结果当作性能结论。

```powershell
python scripts/benchmark_hnsw.py `
  --tenant-id tenant_demo `
  --user-id user_demo `
  --output evaluation/reports/hnsw-benchmark.json `
  --samples 50 `
  --ef-search 40,80,120,200 `
  --minimum-vectors 1000
```
