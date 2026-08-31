# RAG 真实论文切片质量审计（2026-08-31）

## 范围

- 语料：`evaluation/corpus/pdfs/` 中 7 篇文本型联邦学习论文，共 82 页。
- 解析：`multimodal_aware_v3`（PyMuPDF 多模态回退路径）。
- 切片：`scholar_hierarchical_v4`，`target_tokens=450`，`max_tokens=800`。
- 本轮只检查解析与 Chunk 产物，不调用 Embedding，也不测检索排名。
- Docling 主路径未纳入这组结果；Docker Desktop 当前无法启动，不能把回退结果写成 Docling 验收结果。

## 首轮结果

| 论文 | 页数 | Chunk | prose | equation | table | figure | algorithm | 超 800 token | 完全重复 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Blockchain committee consensus | 8 | 35 | 27 | 2 | 1 | 5 | 0 | 0 | 0 |
| Adversarial lens | 19 | 42 | 30 | 5 | 1 | 6 | 0 | 0 | 0 |
| Communication-efficient learning | 11 | 41 | 31 | 1 | 3 | 5 | 1 | 0 | 0 |
| FLchain | 4 | 23 | 14 | 5 | 0 | 3 | 1 | 0 | 0 |
| Byzantine-tolerant gradient descent | 11 | 36 | 27 | 2 | 0 | 7 | 0 | 0 | 1 |
| Practical privacy-preserving framework | 15 | 68 | 42 | 12 | 7 | 6 | 1 | 0 | 0 |
| Privacy-preserving Byzantine-robust FL | 14 | 77 | 43 | 11 | 8 | 9 | 6 | 0 | 0 |

所有原子 Chunk 均有内容，未发现超过 800 token 的 Chunk。首轮发现 1 个完全重复公式 Chunk，以及 4 个短 preamble Chunk（作者、单位或 arXiv 页眉）。

## 已修复问题

### 相同公式编号导致元数据串写

真实论文同一页有两个编号都为 `(1)` 的公式。旧实现使用公式编号作为字典键，把截图、Markdown 与 bbox 写回 manifest；第二个 `(1)` 会覆盖或继承第一个公式的记录，最终产生完全重复的检索 Chunk。

修复后按来源 bbox 匹配具体公式记录，而不是假设编号在页面内唯一。该论文重新解析后仍生成 36 个 Chunk，完全重复数从 1 降为 0，两个公式分别保留自己的 `source_block_ids`、文本和 provenance。

继续核查发现其中一个所谓公式其实是正文复杂度记号 `O(1)`：旧编号正则把末尾 `(1)` 当成公式号，并在去编号时把内容截断为 `O`。编号识别现在要求左括号前不是字母、数字或下划线；同一规则也用于 pypdf 候选和去编号逻辑。最终该论文生成 35 个 Chunk，page 5 的公式由 2 个降为 1 个真实编号公式，完全重复仍为 0；正文中的 `O(1)` 不再被破坏性抽走。

全语料 37 个公式 Chunk 的可渲染性审计另发现 2 个确定的分段函数错误：`Pij` 的 `+1/-1` 概率分布混入说明 prose，`ReLU(x)` 的两个条件分支被压成一行且括号不平衡。恢复器现在只在明确识别到两个完整分支时输出 LaTeX `cases`；真实输出分别成为 `P_{ij}` 的两条概率分支和 `ReLU(x)` 的 `x>0 / x\le0` 分支，花括号与圆括号均平衡。无法完整识别两个分支时仍保留原始低置信度内容，不推测缺失公式。

### 离线解析误初始化 PostgreSQL

`app.papers` 原先在包导入时立即加载 ingestion service，间接初始化数据库设置和连接栈。只运行 PDF 解析审计也会出现 PostgreSQL 超时日志。

公开导出已改为惰性加载。导入 `app.papers.parsing` 或 `app.papers.chunking` 不再加载 `app.papers.ingestion` 与 `app.db.session`，而原有 `from app.papers import PaperInput` 等接口保持兼容。

## 尚未自动过滤的质量信号

4 个短 preamble Chunk 是作者、机构或 arXiv 标识。它们不是理想正文证据，但不能仅按长度删除：标题、摘要、作者和机构仍属于用户要求保存的论文信息。后续应先把首页元数据结构化并记录来源，再从正文检索 Chunk 中排除确认已入元数据表的页眉/作者块，避免启发式误删短摘要。

## 下一步验收

1. Docker daemon 恢复后准备完整 Docling 模型，确认 `actual_parser=scholar_hierarchical_v4`。
2. 用同一 7 篇论文重跑 Docling 主路径统计，并与本文回退路径逐项比较。
3. 在“切片”视图人工检查阅读顺序、短块、公式 Markdown、表格结构、图像裁剪和算法步骤。
4. 将首页元数据结构化后，再决定 preamble 的索引过滤规则。
