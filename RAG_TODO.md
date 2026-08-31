# ScholarAgent RAG 总体 TODO 与完成记录

> 本文档记录 ScholarAgent RAG 子系统从基础建设到最终验收的完整路线，包括已经完成、正在推进和后续计划的工作。它不是仅记录剩余事项的临时清单。

## 1. 范围与边界

### 1.1 RAG 负责范围

RAG 子系统负责以下完整链路：

```text
论文进入系统
  → 论文身份与元数据归一化
  → PDF 解析与结构恢复
  → Chunk 生成
  → Embedding
  → PostgreSQL/pgvector 存储与索引
  → lexical/vector/hybrid 检索
  → rerank 与上下文组织
  → 返回带完整来源信息的 Top-K Chunk
  → 离线评测、回归测试和运行监控
```

### 1.2 与 Agent 的边界

RAG 向 Agent 提供统一、稳定、可审计的检索结果，包括：

- 完整 Chunk 原文；
- 论文 ID、标题和可用元数据；
- 章节路径、页码、Chunk 序号；
- lexical、vector、融合或 rerank 分数；
- 检索模式、告警和降级信息；
- 本地内容是否可以引用；
- 外部候选是否仍需采集、解析和入库。

Agent 负责理解意图、决定何时检索、调用检索接口、组织证据、生成回答、执行引用校验和反思。Agent 不应绕过 RAG 接口直接查询数据库。

### 1.3 当前不做

- [ ] **OCR 与扫描版 PDF 解析：暂不做。** 当前目标语料是从 arXiv、出版社、会议网站等渠道下载的可搜索文本 PDF。解析器检测到文本不足时可以继续标记 `needs_ocr`，但近期不接入 OCR 引擎，也不为扫描件效果设定验收指标。

## 2. 状态说明

- `[x]`：已完成并已有代码、测试或评测结果。
- `[ ]`：尚未完成，需要进入后续开发。
- “部分完成”：已经具备基础能力，但还未达到最终目标。

## 3. 阶段一：PostgreSQL 与 pgvector 基础设施

### 已完成

- [x] 新分支允许使用全新的 PostgreSQL 数据库，不迁移原有 SQLite、JSON 或 Chroma 数据。
- [x] PostgreSQL 成为关系数据和论文检索的统一事实源，不再提供运行时 Chroma/SQLite 回退。
- [x] 使用 PostgreSQL 17 与 `pgvector/pgvector:0.8.5-pg17-bookworm`。
- [x] 使用 Alembic 创建数据库结构和索引。
- [x] `paper_chunks.embedding` 使用 `vector(1024)`。
- [x] 为全文检索建立 `tsvector`/GIN 索引，为向量检索建立 pgvector 索引。
- [x] 数据访问加入 tenant/user 条件和 PostgreSQL RLS 隔离。
- [x] 健康检查能够报告 PostgreSQL、pgvector 和当前 Embedding 配置。
- [x] Docker Compose 包含 PostgreSQL、迁移、后端、Worker、前端等所需服务。

### 后续

- [ ] 增加数据库备份、恢复和迁移回滚的实际演练文档。
- [ ] 增加面向大规模 Chunk 数量的 HNSW 参数和查询延迟压测。
- [x] 建立数据库容量指标：论文、当前 Chunk、ready 向量、失败/待处理任务、Chunk 表字节和索引字节，并通过 `/knowledge/rag/stats` 返回。

参考：

- `docs/superpowers/specs/2026-07-16-postgresql-pgvector-retrieval-design.md`
- `docs/superpowers/plans/2026-07-16-postgresql-pgvector-retrieval.md`
- `alembic/versions/20260716_0001_postgres_foundation.py`

## 4. 阶段二：论文数据模型与存储一致性

### 已完成

- [x] 建立 `papers`、`paper_assets`、`paper_contents`、`paper_pages`、`paper_sections`、`paper_chunks` 和 ingestion/re-embedding job 等数据结构。
- [x] 论文、内容版本、页面、章节、Chunk 和向量之间具有明确关联。
- [x] 论文身份支持 DOI、arXiv ID 和来源标识归一化。
- [x] 同一论文重新解析时使用内容版本替换，只有当前版本参与检索。
- [x] 保存文件 URI、SHA-256、大小、MIME 类型和解析状态。
- [x] 解析成功但 Embedding 失败时保留正文和 lexical 检索能力。
- [x] 更换 Embedding 模型时旧向量会变为 stale，并通过持久化任务重新生成。
- [x] Chunk 保存完整原文、哈希、序号、章节、页码和字符范围。
- [x] 每个内容版本的 parse manifest 保存扁平 `asset_inventory`，统一记录截图名称、类型、页码、来源块、标签和质量状态。
- [x] 资产下载只允许当前内容版本清单引用的安全文件名，并兼容历史 `visual_blocks/equations` manifest。
- [x] 提供只识别、不删除的孤立生成 PNG 计算函数；仅接受 `page_XXX_*.png` 直接子文件，拒绝嵌套/越界路径和非生成文件。
- [x] 论文删除、列表、详情和知识库开关遵守租户隔离。

### 后续

- [ ] 为内容版本替换、Worker 重试和并发上传增加真实 PostgreSQL 集成测试。
- [ ] 明确文件资产的生命周期：论文删除、内容换版、孤立截图和临时文件清理。
- [ ] 在版本级资产清单基础上确定旧内容版本保留期限，并实现事务提交后的孤立 PNG 清理执行器；当前只计算候选，不自动删除。
- [ ] Docker/PostgreSQL 恢复后执行数据库一致性验收，确认 `ready_noncurrent_chunks/ready_missing_vectors/ready_wrong_model` 全为 0；统计与 degraded 状态已实现，真实库数值仍待验收。

## 5. 阶段三：千问 Embedding 与向量生命周期

### 已完成

- [x] 实现 OpenAI 兼容的千问 Embedding 客户端。
- [x] 当前模型统一为 `qwen3.7-text-embedding`。
- [x] 当前向量维度统一为 1024。
- [x] 对返回向量执行数量、维度、有限数值、零向量和归一化检查。
- [x] 支持批量调用、失败重试和批次大小限制。
- [x] 支持在保存配置前探测模型是否真实可用。
- [x] 向量按 Embedding 模型隔离，避免新旧模型向量混用。
- [x] 提供 pending、ready、stale、failed 状态和重新生成向量任务。
- [x] 网站支持填写 Embedding Base URL、API Key、模型名并执行连通性测试。
- [x] 修复 backend、MCP Server 和 Worker 长期缓存旧运行配置的问题；常驻进程会读取 PostgreSQL 中最新的密钥和模型配置。
- [x] 修复论文入库服务缓存旧 Embedding 客户端的问题；每次新入库都会按当前配置创建客户端。
- [x] failed 向量会被前端明确标记为“需要重试”，并可重新入队，不再错误显示“无需重建”。
- [x] 使用真实论文验证 failed 77 → ready 77 的重建闭环，以及中文查询无告警进入 hybrid 检索。

### 后续

- [ ] 在模型用量页面显示本项目累计 Embedding Token、调用次数、失败率和估算费用。
- [x] 增加 Embedding 批处理、超时和限流回退测试：固定 20 条/批，验证跨批顺序、后续批次 429 不重发已完成批次、指数退避、重试耗尽统一异常，以及语义不可用时 lexical 检索保留。
- [x] 形成模型切换验收流程：候选探测不落库 → 保存配置 → 不兼容向量 stale → 创建重建任务 → ready/一致性归零 → 使用新模型完成 hybrid 检索回归；操作检查点见 `docs/operations/STARTUP_CN.md`，真实 Docker 验收仍归入端到端门禁。

## 6. 阶段四：PDF 解析

### 6.1 `legacy_fixed` 基线

- [x] 保留旧 `pypdf` 纯文本解析代码用于对照。
- [x] 保留原有前 50,000 字符截断行为，确保历史基线可复现。
- [x] 明确该策略不作为生产默认值。

### 6.2 `structure_aware_v1`

- [x] 使用 PyMuPDF 提取文本块、坐标和字号。
- [x] 重建单栏/双栏阅读顺序。
- [x] 删除跨页重复的页眉、页脚和页码。
- [x] 识别摘要、引言、相关工作、方法、实验、结论、参考文献等章节。
- [x] 保存页面、文本块、章节、页码范围和字符范围。
- [x] 不再截断论文后部正文。

### 6.3 `formula_aware_v2`

- [x] 保留结构感知解析能力。
- [x] 检测带编号的展示公式及其空间碎片。
- [x] 使用 PyMuPDF 与 `pypdf` 文本候选恢复公式。
- [x] 将可恢复公式保存为 Markdown/LaTeX 展示块。
- [x] 保存公式编号、边界框、原始候选、恢复来源和置信度。
- [x] 对低置信度公式保存源 PDF 截图用于人工核查。

### 6.4 `multimodal_aware_v3`

- [x] 保留公式感知解析能力。
- [x] 检测并区分 equation、table、figure、algorithm。
- [x] 保存对象标签、标题、页码、边界框、Markdown/原文和截图资产。
- [x] 改善 IEEE 表格标题识别。
- [x] 收紧图表截图范围，减少相邻正文被错误截入和大面积留白。
- [x] 在论文正文工作台展示结构化正文、公式、图、表和算法。
- [x] 修复结构化正文页面滚动问题。

### 6.5 `scholar_hierarchical_v4`

- [x] 引入 Docling 作为主解析器，并将其输出归一化到项目现有 `ParsedPaper/Page/Section/Block` 模型，业务层不依赖 Docling 类型。
- [x] 明确关闭 OCR；当前版本只面向网上下载的可搜索文本 PDF。
- [x] Docling 不可用、模型缺失或解析质量不足时自动回退 `multimodal_aware_v3`。
- [x] 回退后的 parser engine、原始异常和目标策略写入 parse manifest，避免静默降级。
- [x] 捕获 Docling 底层模型加载可能产生的 `SystemExit`，不会终止上传 Worker。
- [x] 表格、公式、图片、算法及版面来源信息统一转换为稳定块模型。
- [x] Docling 固定为 `2.123.0`，模型缓存挂载到 Docker 持久化卷。
- [x] Docker 依赖固定为 CPU 版 PyTorch，避免 Docling 构建错误下载整套 CUDA 依赖。
- [x] backend/worker 显式使用共享 `DOCLING_ARTIFACTS_PATH`，并提供 `docling_models` 一次性预取服务下载 layout、TableFormer 和 code/formula 模型（不包含 OCR）。
- [x] `docling_models` 下载后会检查各模型目录的真实文件、Docling/PyTorch 版本和 CPU-only 状态，缺失模型或 CUDA 版 PyTorch 会以非零状态退出。
- [x] 配置 `DOCLING_ARTIFACTS_PATH` 后，上传解析会在加载 Docling/Torch 前轻量检查模型目录；模型不完整时立即显式回退，不在请求中临时下载或长时间加载权重。
- [x] Docling `DocumentConverter` 按模型目录在 Worker 进程内安全复用，避免每篇论文重复初始化数百 MB 模型；共享转换器的实际转换串行执行，避免未知线程安全问题。
- [x] Docling 成功与回退 manifest 统一显式记录 `requested_parser` 和 `actual_parser`，不再依赖调用方推断。
- [x] 离线导入 PDF 解析/切片模块不再初始化 ingestion 与 PostgreSQL 连接栈，评测脚本可在数据库离线时独立运行。
- [x] 浏览器 Worker 与 MCP 镜像不安装 Docling 重依赖，避免拖慢 Playwright 构建。
- [x] 使用真实 4 页联邦学习论文验证缺模型时的回退链路，最终成功生成 25 个 v4 Chunk。
- [x] 使用真实 13 页 IEEE 联邦学习论文完成 v4 切片回退验收：实际解析器为 `multimodal_aware_v3`，切片器为 `scholar_hierarchical_v4`，生成 77 个 Chunk。
- [ ] 网络可稳定访问 Hugging Face 后，补齐 `TableModel04_rs` 并完成 Docling 主路径的多版式真实 PDF 验收。
- [ ] 完成包含 Docling 的 backend/worker 镜像全量构建；当前正在运行的复用镜像尚未安装 Docling，因此会自动回退到 `multimodal_aware_v3`。
- [ ] Docling 主路径验收通过前，v4 仅通过网站运行配置显式选择；不要把“请求 v4”误报为“Docling 实际执行成功”。

### 后续

- [ ] 为不同出版社版式建立解析回归样本：arXiv、IEEE、ACM、Springer、Elsevier。
- [ ] 为公式恢复建立人工标注的正确率评测，而不只依赖检索 Recall。
- [ ] 为表格建立结构完整性指标，包括行列数、表头、合并单元格和标题匹配。
- [ ] 为图片裁剪建立 IoU/人工可用性评测，减少混入正文、漏裁和留白。
- [ ] 为算法建立步骤完整性、输入输出和编号保真度检查。
- [x] 对无法可靠恢复的对象明确输出数值置信度、质量原因、结构内容可用性与原图回退状态；公式和 review/rejected 视觉块在工作台显示低置信度告警，有源图时要求以原图为准，无源图时只保留可审计标题/原文，不伪造结构化内容。

参考：

- `docs/superpowers/plans/2026-07-17-structured-pdf-parsing.md`
- `docs/superpowers/plans/2026-07-18-formula-aware-pdf-parsing.md`
- `docs/superpowers/plans/2026-07-18-multimodal-paper-blocks.md`

## 7. 阶段五：Chunk 策略

### 已完成

- [x] 保留 `legacy_fixed` 固定长度/段落切片基线。
- [x] 实现 `structure_aware_v1` 章节内切片，不跨章节合并。
- [x] 优先沿段落和句子边界切分，保留完整文本单元重叠。
- [x] 参考文献、致谢、页眉和页脚不进入检索 Chunk。
- [x] Embedding 文本加入论文标题和章节路径，但返回内容仍是完整原文。
- [x] 实现 `formula_aware_v2` 公式边界保护。
- [x] 实现 `multimodal_aware_v3` 图、表、公式、算法原子 Chunk。
- [x] 从普通正文中移除已生成原子块的内容，降低重复索引。
- [x] 实现 `scholar_hierarchical_v4` 层级语义切片：正文按软 token 目标聚合且不盲目重叠。
- [x] 公式 Chunk 保留 LaTeX/Markdown 原文，并向 Embedding 文本注入前后解释上下文。
- [x] 大表按完整行拆分，每块重复表题和表头。
- [x] 长算法按完整步骤拆分，每块重复算法标题，步骤不重复。
- [x] 图、表、公式和算法保存 `chunk_type`、父章节、来源块 ID、上下文和坐标 provenance。
- [x] 统一检索 hit 返回 `context_before/context_after` 和前后稳定 Chunk UUID，Top-K 原始 Chunk 身份与完整原文不变。
- [x] 原始 `content` 与增强后的 `embedding_content` 分离，前端调试仍显示完整原文。
- [x] 五种策略可配置、可重复运行并可在同一评测集上比较。
- [x] 论文结构接口返回当前内容版本的全部 Chunk，并按 `chunk_index` 排序，包含类型、章节路径、页码、完整原文、字符数、token 数、来源块和向量状态。
- [x] 论文结构接口返回当前内容版本的扁平资产清单，供前端展示、下载白名单和后续生命周期管理共用。
- [x] 使用真实 13 页 IEEE 论文生成并检查 77 个 v4 Chunk：正文 43、公式 11、表格 8、图片 9、算法 6，序号为 0–76，向量全部 ready。
- [x] 修复同页重复公式编号按 label 写回元数据导致内容串写的问题；改为按来源 bbox 匹配，真实论文重复 Chunk 从 1 降为 0。
- [x] 修复复杂度记号 `O(1)` 被误认成公式编号并截断为 `O` 的问题；真实论文移除 1 个假公式 Chunk，同时保留真正的 `(1)` 公式。
- [x] 为两分支条件/概率公式增加保守的 LaTeX `cases` 恢复；真实 `ReLU(x)` 与 `P_{ij}` 公式不再混入 prose 或产生不平衡括号。
- [x] 恢复被 PDF 文本层压成单行的算法步骤，支持 `1:`、`1.` 与保守的裸数字动作编号；去掉代码围栏噪声并补全算法编号/标题。
- [x] 对现存 54 个真实 `fig/figure` PNG 做空白/留白像素审计：无空白图，有效内容 bbox 均 ≥55%，单侧留白均 ≤20%。

### 后续

- [x] 实现父子检索：小 Chunk 用于召回，Agent 可按命中 Chunk UUID 分别读取完整父章节或带预算的连续相邻 Chunk。
- [x] 实现检索后相邻 Chunk 合并：Top-K 排名和原始 Chunk 保持不变，额外返回同论文、同内容版本、同章节且序号连续的 `merged_contexts`，正文按论文顺序拼接并保留全部 Chunk ID 与稳定引用键；MCP/Agent 链路不再按论文误删多个 Chunk。
- [ ] 实现真正的语义近重复和同对象分片结果去重；当前不对表格/算法分片做模糊去重，避免把重复表头或算法标题下的不同证据误删。
- [x] 已实现同一论文内“忽略空白和大小写后正文完全相同”的检索结果去重；不同证据和不同论文仍保留。真正依赖语义模型的近重复仍待 reranker/评测后决定。
- [x] 已实现保守的相邻高重叠 prose 去重：仅同论文且共享 source block 或同章节相邻 Chunk 才比较，长度至少 120 字符且相似度达到 0.92；跨论文、跨章节和表格/算法分片保持不变。
- [ ] 比较不同 `chunk_size`、overlap 和原子块上下文注入参数。
- [ ] 为摘要、结论、公式解释、图表标题等内容设计可控的上下文增强，而不是无条件重复标题。
- [x] 记录每个 Chunk 的父级章节和来源对象 ID，为后续父子检索提供稳定关系。
- [x] 将相邻节点从当前上下文文本升级为可导航的 `previous_chunk_id/next_chunk_id` 稳定关系。

## 8. 阶段六：统一检索接口

### 已完成

- [x] 建立统一 `RetrievalRequest`、`RetrievalResponse`、`LocalHit` 和外部候选契约。
- [x] 排名和返回单位统一为 Chunk，而不是论文。
- [x] Top-K 允许返回同一论文的多个 Chunk。
- [x] 返回完整 Chunk 原文，不在后端或前端截断调试内容。
- [x] 返回论文、章节、页码、Chunk 序号和可引用状态。
- [x] 返回 Chunk 类型、父章节、来源块 ID 和结构化 provenance。
- [x] 返回原子块前后解释上下文及前后稳定 Chunk ID，供 Agent/UI 按需展开，不自动消耗额外回答 token。
- [x] 实现 PostgreSQL lexical 检索。
- [x] 实现 pgvector cosine semantic 检索。
- [x] 使用 RRF 融合 lexical 与 vector 排名。
- [x] Embedding 不可用时降级到 lexical 检索。
- [x] 为常见中文查询增加词面别名/英文术语兜底。
- [x] 本地已入库内容与外部候选分开；外部候选在解析入库前不可引用。
- [x] Agent、写作流程、API 和 MCP 统一消费检索服务。

### 后续

- [ ] 接入千问 reranker 或可替换的交叉编码 reranker。
- [ ] 比较“仅 vector、仅 lexical、RRF hybrid、hybrid + reranker”四种生产检索策略。
- [x] 增加中英双向查询扩展：中文、英文术语、缩写和混合查询映射到同一学术概念组；短缩写使用词边界避免误触发，响应回显 `query_expansions`。
- [x] 支持本地候选按论文、年份区间、作者、发表渠道、章节和对象类型过滤；lexical 与 vector 使用同一组 SQL 条件，响应回显规范化后的 `filters`。
- [x] 为不同查询类型设置候选池大小：普通概念保持 80，短缩写/代码类扩大到 120，公式/表格/图片/算法扩大到 160，多对象筛选扩大到 180；调用方显式指定时不覆盖，响应回显 `query_type/candidate_limit`。
- [x] 增加父子检索与自适应论文多样性约束：首轮限制每篇论文占位，跨论文候选不足时按原 RRF 顺序回填，避免单篇知识库返回不足 Top-K。
- [x] 输出可审计的排名明细：`lexical_rank`、`vector_rank`、`rrf_score`、`rerank_score`（未启用时为 null）和 `final_rank`；旧 `score` 保持为 RRF 分数以兼容现有前端。
- [x] 制定检索超时和降级策略：交互语义链路默认总预算 8 秒，可通过 `SCHOLAR_RAG_SEMANTIC_TIMEOUT_SECONDS` 配置；超时或 Embedding 失败时取消 semantic 并保留 lexical 结果和警告。

## 9. 阶段七：论文元数据

### 已完成或部分完成

- [x] 标题基础字段和 PDF metadata 标题候选。
- [x] 作者基础字段支持存储和手工/API 输入。
- [x] 摘要基础字段支持存储。
- [x] 发表时间基础字段支持存储和手工/API 输入。
- [x] DOI 提取、归一化和唯一性约束。
- [x] arXiv ID 提取、归一化和唯一性约束。
- [x] GitHub/GitLab 代码或项目链接初步提取。

### 后续

- [ ] 提高正文首页标题识别质量，并与 PDF metadata、arXiv/Crossref 结果交叉校验。
- [ ] 增加标题中文翻译，明确原始标题与派生翻译不能相互覆盖。
- [ ] 从首页和外部权威元数据中提取完整作者列表。
- [ ] 提取作者机构及作者—机构对应关系。
- [ ] 提取并规范化发表时间。
- [ ] 提取发表渠道，包括期刊、会议、预印本平台。
- [ ] 合并正文、PDF metadata、Crossref、arXiv 中的 DOI/arXiv 信息并记录来源。
- [x] 元数据契约区分代码仓库、项目主页、数据集和补充材料链接；当前 PDF 文本自动识别代码仓库，其他类别优先接收上传/外部权威元数据，后续继续扩展自动发现率。
- [ ] 识别论文类型，例如研究论文、综述、系统论文、方法论文和预印本。
- [x] 为标题、标题翻译、作者、机构、时间、渠道、DOI、arXiv、分类链接和论文类型统一保存 `value/source/confidence/user_edited`；重解析保留人工修正值，未知字段显式标记 `not_found/not_generated` 而不是猜测。
- [x] 在论文工作台完整展示并允许修正上述字段；每个字段显示 `source/confidence/user_edited`，保存使用独立的 tenant/user scoped metadata PATCH，不触发重新解析、重切片或向量重建。

目标论文信息：

```text
标题
标题翻译
作者
机构
发表时间
发表渠道
DOI
arXiv
代码 / 项目
论文类型
```

## 10. 阶段八：前端调试与运维界面

### 已完成

- [x] 提供模型路由和运行配置界面。
- [x] 支持填写 API Key、Base URL 和模型名。
- [x] 支持模型连通性检测。
- [x] 支持查看 Embedding 状态并重新生成向量。
- [x] 支持在网站运行配置中选择 PDF 解析策略和 Chunk 策略，包括 v4 与全部历史基线。
- [x] RAG 验证界面返回 Top-K Chunk。
- [x] 显示完整 Chunk 原文、论文、章节、页码、score、lexical rank 和 vector rank。
- [x] 论文工作台支持原文 PDF、结构化正文和图表算法视图。
- [x] 论文工作台增加独立“切片”视图，不经过检索即可按顺序浏览当前版本的全部 Chunk。
- [x] 切片视图完整展示原文而不截断，并支持按正文、公式、表格、图片、算法和代码类型过滤。
- [x] 切片浏览与 RAG Top-K 检索验证明确分离：前者检查解析/切片边界，后者检查召回和排序。
- [x] 修复上传 413、正文滚动和中文检索回退等问题。

### 后续

- [ ] 增加单次查询调试抽屉，展示 query embedding、候选池和各阶段排名变化。
- [ ] 增加策略切换，允许在相同查询下并排比较 vector、lexical、hybrid 和 rerank。
- [x] 增加 Chunk 父子关系、相邻 Chunk 和连续上下文预览；结构接口返回父章节、前后稳定 Chunk ID 与原子块解释上下文，切片视图在不替换完整原文的前提下按需展开前一/当前/后一 Chunk。
- [ ] 增加低置信度公式、表格、图片和算法的人工修正入口。
- [x] 增加论文元数据完整度和待修正字段提示；论文信息页显示完整字段数量，并将缺失或置信度低于 0.7 的字段列为待修正。

## 11. 阶段九：RAG 评测体系

### 已完成

- [x] 建立 `evaluation/corpus.jsonl` 固定论文身份、路径、页数和 SHA-256。
- [x] 建立 `evaluation/queries.jsonl` 保存中英文查询和策略无关的人工证据。
- [x] 当前语料包含 7 篇联邦学习论文，共 82 页。
- [x] 当前查询包含 28 条，其中中文 21 条、英文 7 条。
- [x] 每条查询标注来源论文、页码、证据短语和证据原文。
- [x] 使用固定千问 Embedding 比较四种解析/切片策略。
- [x] 输出完整 Top-10 排名、Chunk 原文和来源信息。
- [x] 计算 Recall@K、Precision@K、MRR 和 NDCG@K。
- [x] 使用语料指纹和查询指纹保证跨版本结果可比。
- [x] 完成第一轮四策略基准报告。

第一轮结果摘要：

- `structure_aware_v1` 的纯文本 Top-1 和 MRR 最好。
- `formula_aware_v2` 与结构感知基本持平，同时保留更好的公式边界。
- `multimodal_aware_v3` 保真度更高，但更多、更短的原子 Chunk 使 Top-1 略有下降。
- `legacy_fixed` 受语义边界和 50,000 字符截断影响，只保留为基线。

### 后续

- [ ] 扩充到更多论文和更多版式，避免 7 篇论文产生偶然结论。
- [ ] 增加公式专项查询及人工公式证据。
- [ ] 增加表格专项查询及单元格/行级证据。
- [ ] 增加图片专项查询及图题、图中文字和视觉证据。
- [ ] 增加算法专项查询及步骤级证据。
- [ ] 分别报告中文、英文、缩写和跨语言查询指标。
- [ ] 建立生产检索评测，比较 lexical、vector、hybrid 和 hybrid + reranker。
- [ ] 增加查询级失败分类：未解析、未切入、未召回、排序过低、证据判定问题。
- [ ] 增加检索延迟、Embedding Token、索引大小和调用成本指标。
- [ ] 每次调整解析、切片、Embedding 或排序后自动生成新报告并与基线比较。

参考：

- `evaluation/README.md`
- `evaluation/reports/analysis.md`
- `evaluation/reports/chunk-comparison.md`
- `evaluation/reports/chunk-comparison.json`

## 12. 阶段十：RAG 与 Agent 集成验收

### 已完成

- [x] Agent、写作 Agent、MCP 和 Web API 使用统一检索契约。
- [x] 本地 ready Chunk 标记为可引用。
- [x] 外部搜索候选在采集和入库前保持不可引用。
- [x] 检索结果包含完整 Chunk 和来源信息，能够支持引用定位。

### RAG 侧后续

- [x] 为 Agent 提供稳定的父级上下文展开接口；嵌套小节优先返回父章节，否则返回当前章节，且只读当前 content version。
- [x] 为 Agent 提供按 token budget 选择完整相邻 Chunk 的能力；中心 Chunk 始终完整保留，任何存储 Chunk 都不会被截断。
- [x] 为每个可引用检索 hit 提供 `citation` locator：稳定 key、paper ID、content version、Chunk UUID、页码范围、章节 ID/路径；回答层是否采用该引用仍属于 Agent 责任。
- [ ] 增加 Agent 调用场景的检索回放：保存查询、策略、候选和最终上下文。
- [ ] 建立“RAG 已正确返回证据，但 Agent 未采用”与“RAG 未返回证据”的错误归因。

### 不属于 RAG 的工作

- Agent 意图规划、任务分解和多 Agent 调度。
- 写作风格、章节生成和反思重写。
- Agent 长短期记忆和会话压缩。
- 引用文本如何融入最终答案的语言生成策略。

## 13. 阶段十一：测试、文档与发布质量

### 已完成

- [x] 主要解析、切片、Embedding、repository、retrieval 和 evaluation 模块具有单元测试。
- [x] 已使用真实联邦学习论文进行浏览器和 API 调试。
- [x] 已编写中文启动文档和 Docker Compose 启动流程。
- [x] 已保留各阶段设计、实施计划和评测报告。

### 后续

- [x] 修复 Windows Python 3.14 默认 ProactorEventLoop 与异步 psycopg 不兼容：数据库运行时入口在 Windows 提前切换 Selector policy，并有平台专项测试；Python 3.16 移除 policy API 时需改用宿主 `loop_factory`。
- [ ] Docker 启动后重新执行 PostgreSQL、上传、解析、索引、检索和页面展示的完整 E2E。
- [x] 建立固定 RAG 回归命令 `python scripts/run_rag_regression.py` 和 GitHub Actions 任务；固定清单显式排除 Docker、真实 PostgreSQL、浏览器和外部模型 API 测试，E2E 仍是独立门禁。
- [x] 更新仍提到 Chroma、SQLite/TinyDB fallback 或“尚未迁移 PostgreSQL/pgvector”的过时架构文档与测试注释；SQLite SQL 翻译单测仅保留为旧调用兼容，不代表存储回退。
- [x] 将 `docs/superpowers/plans/` 明确标注为历史实施计划；其中复选框不再代表当前状态，RAG 进度只以本文件为准。
- [ ] 清理 `tmp/` 中的调试 PDF、截图和 JSON 结果，防止测试资产混入正式提交。
- [x] 静态审计 Git 跟踪内容：未跟踪私有 `.env`、常见真实 Key、上传 PDF/PNG 或 `storage/tmp/models/logs` 运行资产；发布前仍由门禁重复检查。
- [ ] 在提交和发布前运行完整测试、`docker compose config` 和浏览器验收。

## 14. 后续执行优先级

> 当前决定：先暂停新增评测工作，优先完善一条最佳 PDF 解析与切片主路径。已有评测代码、语料和报告全部保留，待解析/切片质量稳定后再继续扩充。

### P0：完善最佳解析与切片主路径

- [ ] 完成包含 CPU PyTorch 与 Docling 的 backend/worker 镜像构建，确认不再下载 CUDA 依赖。
- [ ] 补齐 Docling 所需模型并通过主解析路径验收，确认 manifest 中 actual parser 确实为 Docling。
- [ ] 使用 IEEE、arXiv、ACM、Springer、Elsevier 文本型 PDF 人工检查阅读顺序、章节、公式、表格、图片和算法。
- [ ] 直接使用“切片”视图逐篇检查边界质量，记录过长、过短、跨章节、上下文不足、重复和乱码 Chunk。
- [ ] 修复公式 Markdown、表格结构、图片裁剪和算法步骤的剩余质量问题。
- [ ] 完成上传 → 解析 → v4 切片 → Embedding → 切片浏览的 Docker 端到端回归。
- [ ] 主路径稳定后再决定是否把 `scholar_hierarchical_v4` 设为默认策略。

### P1：提高 Top-1 和上下文质量

- [ ] 接入 reranker。
- [x] 实现父子 Chunk、相邻合并和重复结果去重；统一检索返回父章节/邻接关系及独立 `merged_contexts`，切片工作台可展开连续上下文，去重策略保留跨论文和不同结构对象证据。
- [x] 增加中文/英文术语与缩写的双向查询扩展，并为无 Embedding/超时降级建立 lexical 回归测试。
- [ ] 重点观察 Recall@1、MRR、NDCG@3 和下游上下文 Token。

### P2：补全论文信息和专项评测

- [ ] 完成标题翻译、作者、机构、时间、渠道、代码/项目和论文类型。
- [ ] 扩充公式、表格、图片、算法专项数据集。
- [ ] 建立对象解析质量与检索质量两套独立指标。

### P3：恢复评测、规模和长期维护

- [ ] 恢复生产检索评测，比较 lexical、vector、hybrid 和 hybrid + reranker。
- [ ] 扩展评测程序，使其使用与网站一致的 PostgreSQL 查询链路。
- [ ] 扩大语料规模并进行数据库与检索性能压测。
- [ ] 增加模型调用成本和索引容量监控。
- [ ] 建立可重复的版本回归、报告归档和发布门禁。

## 15. RAG 最终完成标准

只有同时满足以下条件，RAG 子系统才可以标记为稳定版本：

- [ ] 文本型论文 PDF 能稳定解析，正文顺序、章节和来源页码可追溯。
- [ ] 论文元数据达到目标字段要求，并记录来源与人工修正状态。
- [ ] 图、表、公式和算法具有可审计的结构化内容或可靠原图回退。
- [ ] Chunk 不跨无关章节，公式/表格/图片/算法不会被破坏性切分。
- [ ] PostgreSQL 中不存在当前内容版本与 ready 向量不一致的问题。
- [ ] lexical、vector、hybrid 和 rerank 都可以独立调试和评测。
- [ ] 中文和英文查询在固定评测集上达到确定的 Recall@K、MRR 和 NDCG 门槛。
- [ ] 检索结果向 Agent 提供完整原文、论文、章节、页码、分数和可引用状态。
- [ ] Embedding 或 reranker 不可用时具有明确、可测试的降级行为。
- [ ] 上传、解析、索引、检索、重新生成向量和删除流程通过 Docker E2E。
- [ ] 所有结果可以通过语料指纹、查询指纹、模型和策略版本复现。

## 16. 当前里程碑

当前已经完成：

```text
PostgreSQL/pgvector 基础
  → 论文模型与存储一致性
  → 千问 Embedding
  → 结构/公式/多模态 PDF 解析
  → Docling 主解析 + PyMuPDF 自动回退
  → 五种 Chunk 策略（含 scholar_hierarchical_v4）
  → 当前版本全部 Chunk 的独立前端浏览与类型过滤
  → Top-K Chunk 与混合检索
  → 前端调试能力
  → 第一版人工评测集与四策略基准
```

### 2026-08-29 暂停点

- 当前示例论文内容版本为 v22。
- 实际解析器为 `multimodal_aware_v3`（Docling 未安装后的显式回退）。
- 切片策略为 `scholar_hierarchical_v4`，共有 77 个 Chunk，向量状态为 ready 77 / failed 0。
- 网站已经能够直接查看完整切片，不需要通过 RAG 检索结果间接观察。
- backend、MCP Server、Worker 的运行配置刷新和 Embedding 重试链路已经修复。
- 浏览器自动点击验收受本机浏览器连接组件路径错误影响，API、静态资源和渲染器测试均已通过；下次继续时先人工打开“知识库 → 论文 → 切片”确认界面。

下一次继续开发时，从 P0 的 Docling CPU 镜像完整构建和真实主路径验收开始；随后直接利用“切片”视图改进边界质量。评测、reranker、父子检索和元数据补全暂时排在其后，不包含 OCR 与扫描件解析。

### 2026-08-31 Goal 续跑记录

- 已增加 Docling 模型 `prepare/check` 诊断，模型不完整或不是 CPU PyTorch 时不会误报 ready。
- 当前 Docker Desktop 4.69.0 在损坏的 `%LOCALAPPDATA%\Docker\run\dockerInference` ReparsePoint 上稳定复现错误 1920 并崩溃；当前工具不能删除该对象，恢复步骤已写入中文启动说明。
- Windows Python 3.14 的 async psycopg 已从 Proactor 切到 Selector event loop；专项测试通过。原先两个知识库集成用例不再抛 `InterfaceError`，但在 Docker daemon 不可用时会继续等待 PostgreSQL，不能把这部分误报为 E2E 通过。
- 已建立无外部服务依赖的固定 RAG 回归入口与 CI；本地首次清单审计排除了会真实写库的 `test_paper_acquisition`，最终 164 个解析、切片、公式、多模态、仓储、Embedding、检索和评测测试通过。
- `/knowledge/rag/stats` 已增加容量与 ready 向量一致性指标；发现非当前版本 ready Chunk、ready 但无向量或模型不一致时返回 `consistency_status=degraded`，真实数据库归零验收等待 Docker 恢复。
- Docker daemon 恢复后，先执行模型准备命令，再完成 backend/worker 全量构建和真实 PDF 主路径验收；这两项仍保持未完成。
- 已对 7 篇、82 页真实文本 PDF 跑 `multimodal_aware_v3` 回退解析 + `scholar_hierarchical_v4` 切片审计；所有原子 Chunk 非空且无超 800 token 项，详细记录见 `docs/operations/RAG_CHUNK_AUDIT_2026-08-31.md`。
- 审计发现并修复同页重复公式编号造成的元数据串写/重复 Chunk；另记录 4 个作者、机构或 arXiv 标识短 preamble Chunk，待首页元数据结构化后安全排除，不使用长度阈值直接误删。
- 公式编号抽取、pypdf 候选选择和去编号已区分 `O(1)` 与真正的独立 `(1)` 标签；对应真实论文由 36 个 Chunk/2 个公式修正为 35 个 Chunk/1 个真实公式。
- 已审计 7 篇论文的 37 个公式 Chunk，并将 2 个明确的分段函数恢复为平衡、可渲染的 LaTeX `cases`；不满足完整双分支模式的内容不做猜测式转换。
- 已审计 20 个表格与 9 个算法对象：19 个回退表格无可靠单元格，继续保留原图和 review 标记等待 TableFormer；真实 IEEE 论文的 6 个算法已由 3 行恢复为 8–14 行步骤，最大 227 token。
- 已审计现存 54 个 figure PNG，未复现大面积留白；但当前 figure Chunk 为 41 个，说明历史解析会残留候选资产，后续需按 content version 建立资产清单和安全清理策略，不能粗暴删除旧目录。
- 已建立 content-version 级 `asset_inventory` 并由结构 API/下载白名单统一消费；旧 manifest 自动归一化。孤立资产目前只做安全候选计算，待确定旧版本保留策略后才执行删除。
- 已在 RRF Top-K 后抑制同论文完全重复正文，并返回 `context_before/context_after/previous_chunk_id/next_chunk_id`；同论文不同 Chunk 与跨论文相同文字不会被误删。
- 已增加 `GET /knowledge/rag/chunks/{chunk_id}/context`：严格按 tenant/user、知识库状态和当前 content version 展开相邻 Chunk；按距离和 token budget 选择连续整块，预算小于中心块时仍保留中心全文并返回 `budget_exceeded=true`。
- 已增加 `GET /knowledge/rag/chunks/{chunk_id}/parent`：召回小 Chunk 后可按需读取完整父章节，返回章节 ID、标题、类型、路径、页码、字符数和估算 token 数，不自动膨胀每次 Top-K 响应。
- 检索 hit 已明确输出 lexical/vector 排名、RRF 分数、rerank 分数占位与最终顺序；未接 reranker 前 `rerank_score=null`，不伪造模型分数。
- 交互检索已增加独立于批量入库的语义总超时；查询 Embedding 或 pgvector 候选超过预算时取消语义链路并返回 lexical 结果，避免千问重试把页面阻塞到客户端超时。
- 统一检索已支持 `paper_id/year_from/year_to/author/venue/section/chunk_type` 结构化过滤；过滤发生在 lexical/pgvector 候选生成阶段而非 Top-K 后处理，响应会回显实际过滤条件供调试。
- lexical 查询扩展已从单向中文别名升级为中英术语/缩写双向概念组；覆盖 FL、RAG、DP、FHE、SMPC、non-IID、LLM、GNN 等常见科研术语，短缩写按完整 token 匹配，响应回显 `query_expansions`。
- RRF 后增加自适应论文多样性：默认首轮每篇最多 3 个 Chunk，并在跨论文候选不足时回填被延后的同篇证据；响应 `ranking_policy` 回显策略，可用 `SCHOLAR_RAG_MAX_CHUNKS_PER_PAPER` 调整，设为 0 可关闭。
- 检索后处理增加保守的相邻高重叠 prose 抑制；候选只与共享 source block 或同章节相邻位置比较，避免候选池增大时做全量二次比较，也明确不对重复表头/算法标题做模糊去重。
- lexical/vector 命中现在携带当前 `content_version` 并生成结构化 `citation` locator；引用 key 形如 `{paper_id}@v{content_version}#{chunk_uuid}`，论文换版后不会把旧 Chunk 静默解释成新版本证据。
- 已解除论文解析包与数据库初始化的导入时耦合，离线 PDF 批处理不再产生 PostgreSQL 连接超时。
- 已将 Docling 2.123.0 的必需模型目录固定为轻量检查清单，检查过程不再导入 Docling/Torch；缺模型的 4 页真实论文显式回退总耗时由 17.154 秒降至 2.826 秒。
- 已增加按模型目录复用 Docling 转换器的进程内缓存，待 Docker 恢复并补齐模型后验证连续解析多篇论文时只初始化一次模型。
