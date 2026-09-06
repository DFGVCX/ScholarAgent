# PDF Crop and Formula Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复公式乱码/正文串入、双栏图像串区、算法正文串入和视觉资产大留白，同时保持现有数据接口与旧解析方案兼容。

**Architecture:** 在公式分组阶段增加栏位与公式型文本约束，在公式恢复阶段统一 Unicode 数学符号；视觉提取从“全页绘图对象并集”改为“标题栏位 + 相邻结构边界”，渲染后执行像素级去白边；前端始终暴露公式源码并 eager 加载视觉调试资产。

**Tech Stack:** Python 3.11、PyMuPDF、Pillow、unittest、原生 HTML/JavaScript、KaTeX、Docker Compose。

---

## Task 1: 锁定公式回归行为

- [x] 在 `tests/test_formula_parsing.py` 增加 Unicode 运算符转 LaTeX 的失败测试。
- [x] 在 `tests/test_pdf_parsing.py` 增加相邻长正文不得被公式分组吸收的失败测试。
- [x] 断言公式 `ParsedBlock.metadata` 包含原文、LaTeX、置信度和恢复来源。
- [x] 运行公式相关测试，确认新测试先失败。
- [x] 修改 `app/papers/formulas.py` 与 `app/papers/parsing.py`，以最小实现通过测试。

## Task 2: 锁定栏位感知裁剪与算法边界

- [x] 在 `tests/test_multimodal_parsing.py` 构造双栏“左表右图”PDF，断言图像 bbox 不跨栏且不包含页眉。
- [x] 构造同栏连续两图 PDF，断言后一幅图的 bbox 不回卷到前一幅。
- [x] 在算法后加入长段正文，断言正文不进入算法 markdown 和 bbox。
- [x] 运行多模态测试，确认新测试先失败。
- [x] 修改 `app/papers/visuals.py`：用标题栏位和相邻结构边界生成候选区，并在渲染后去白边。

## Task 3: 修复前端调试展示

- [x] 在 `tests/test_model_settings_ui.py` 增加公式源码可见与算法资产 eager 加载的失败测试。
- [x] 运行 UI 静态测试，确认新测试先失败。
- [x] 修改 `frontend/dist/app.html`，展示公式源码/恢复信息，并让视觉调试资产立即加载、按自然高度显示。

## Task 4: 真实论文与系统级验证

- [x] 运行公式、多模态、解析与 UI 定向测试。
- [x] 用 `tmp/pdfs/privacy-paper.pdf` 本地重新解析，核对公式元数据以及 Fig. 1、Fig. 2、Fig. 4、Algorithm 1 的 bbox 和资产。
- [x] 视觉检查新生成的代表性图片，确认不串栏、不串图、无大面积无意义留白。
- [x] 重建并重启受影响的 Docker 服务，重新导入论文。
- [x] 通过结构 API、RAG API 和浏览器检查正文公式、图表算法卡片与资产加载。
- [x] 运行完整测试集并记录与基线无关的既有失败。
- [x] 提交本次实现，保留 `tmp/` 为未跟踪的用户诊断材料，不推送或合并。
