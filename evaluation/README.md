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
