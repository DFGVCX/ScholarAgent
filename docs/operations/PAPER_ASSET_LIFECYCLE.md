# 论文文件与解析资产生命周期

本文档定义 RAG 子系统对源论文、结构化截图、历史内容版本和临时文件的处理边界。目标是避免派生 PNG 无限累积，同时绝不因清理失败破坏已提交的论文数据。

## 资产分类

| 类型 | 示例 | 事实来源 | 当前保留策略 |
| --- | --- | --- | --- |
| 源论文文件 | `uploads/<tenant>/<user>/*.pdf` | `paper_assets` | 论文软删除和内容换版均保留；当前没有自动硬删除 |
| 当前解析派生图 | `<pdf_stem>_assets/page_001_figure_1.png` | 当前 `paper_contents.parse_manifest.asset_inventory` | 当前 manifest 引用期间保留 |
| 旧解析派生图 | 当前 manifest 不再引用的安全 `page_XXX_*.png` | 文件系统候选 | 新内容版本事务提交后立即清理，历史图片版本保留数为 0 |
| 非生成文件 | `notes.txt`、人工附件等 | 文件系统 | 清理器无权删除 |
| 嵌套文件或符号链接 | 子目录、链接 | 文件系统 | 清理器无权删除 |
| 下载/浏览器临时文件 | Worker staging 文件 | 对应 Worker | 不属于论文资产清理器范围 |

数据库中的历史 `paper_contents`、页面、章节和 Chunk 仍可用于审计，但当前产品只向用户暴露当前内容版本。派生图片是当前版本缓存，不承诺历史版本图片回放；如以后需要历史视觉回放，应先把图片路径改为按 `content_uuid` 分目录，再调整保留期限。

## 内容换版流程

```text
解析 PDF 并生成新 PNG
  → 在 PostgreSQL 事务中写入新内容版本和 manifest
  → 事务成功提交
  → 以当前 manifest 计算孤立 PNG
  → 删除安全、直接子级、非符号链接的 page_XXX_*.png
  → 继续 Embedding
```

如果数据库事务失败，不执行清理。如果清理失败，已提交内容和后续 Embedding 不回滚；遗留文件会在下一次成功换版时再次成为候选。

## 删除边界

- `DELETE /knowledge/{paper_id}` 当前是数据库软删除，只关闭知识库可见性，不删除源 PDF 或派生图。
- 自动清理器只能处理 PDF 同目录下 `<stem>_assets` 的直接子文件。
- 文件名必须匹配 `page_XXX_*.png`，并且不在当前 manifest 的扁平资产清单中。
- 源 PDF、任意非 PNG、嵌套路径、越界路径和符号链接一律跳过。
- 未来实现硬删除时，需要单独的确认令牌、租户目录校验、审计记录和可配置保留期；不得复用当前孤立 PNG 清理器删除源文件。

## 验证

离线回归覆盖：引用图片保留、孤立生成 PNG 删除、非生成文件保留、嵌套图片保留，以及清理发生在内容版本事务成功退出之后。运行：

```powershell
python -m unittest tests.test_paper_assets tests.test_paper_ingestion
```
