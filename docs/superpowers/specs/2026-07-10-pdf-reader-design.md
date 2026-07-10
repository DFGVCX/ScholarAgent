# PDF.js 在线阅读器 设计文档

> 日期：2026-07-10
> 状态：已确认，待实施
> 前置：Phase 2 完成（批注 API、智能分块就绪）

## 一、目标

在知识库中点击论文 → 跳转独立全屏页面 `/reader/{paperId}`，使用 pdf.js 渲染 PDF，支持翻页/缩放/文本选择高亮，批注数据通过已有 API 持久化到 `scholar_annotations` 表。

## 二、技术选型

| 决策 | 选择 | 理由 |
|------|------|------|
| pdf.js 加载方式 | CDN（`cdnjs.cloudflare.com`） | 不膨胀 app.html，浏览器缓存 |
| 批注交互 | 鼠标选择文本 → 弹出工具条 | 自然，类 Zotero/Notability |
| 页面路由 | `/reader/{paperId}` | 独立全屏 |
| 前端构建 | Vite（现有） | 不改成 |

## 三、页面布局

```
┌────────────┬───────────────────┬────────────┐
│ 📑 缩略图    │                   │ 📝 批注面板  │
│ 📑 目录     │   pdf.js Canvas   │ (可收起)    │
│ (可收起)    │   工具栏：翻页/缩放   │            │
└────────────┴───────────────────┴────────────┘
```

三栏布局，左右侧栏可独立收起，中间 PDF 区域自适应。

## 四、功能范围（第一版）

| 功能 | 说明 |
|------|------|
| PDF 渲染 | pdf.js canvas，支持多页 |
| 翻页 | 上/下按钮 + 页码跳转输入框 |
| 缩放 | 放大/缩小/适应宽度按钮 |
| 文本选择高亮 | 鼠标选中 PDF 文字 → 弹出工具条（颜色选择 + 确认） |
| 文字批注 | 从工具条打开批注输入框，保存在选中位置 |
| 批注加载 | 进入页面时从 `GET /knowledge/files/{paperId}/annotations` 加载已有批注 |
| 批注保存 | 确认高亮/批注后调 `POST /knowledge/files/{paperId}/annotations` 全量保存 |
| 左侧栏（骨架） | 缩略图/目录占位，UI 架子搭好，功能后续迭代 |
| 右侧栏（骨架） | 批注列表/笔记面板占位，切换 tab 的 UI 架子 |

## 五、批注锚点

```
{
  paper_id: "paper:pdf:abc123",
  page: 3,
  annotation_type: "highlight" | "note",
  color: "#ffeb3b",
  points: [{x: 0.1, y: 0.2}, {x: 0.2, y: 0.3}],  // 相对页面坐标（0-1 归一化）
  content: "这句有问题"                                 // note 类型才有
}
```

坐标归一化到 0-1（相对页面宽高），避免缩放后位置错乱。

## 六、新增后端端点

| 端点 | 说明 |
|------|------|
| `GET /knowledge/files/{paperId}/pdf-info` | 🆕 返回 PDF 页数、文件大小、文件名 |

其他全部复用现有 API（文件服务、批注 CRUD）。

## 七、前端改动

| 改动 | 说明 |
|------|------|
| 新建 `frontend/src/pages/reader/` | Reader 页面组件 + pdf.js 集成 |
| 修改 `api/client.ts` | 新增 `getAnnotations`、`saveAnnotations`、`getPdfInfo` 方法 |
| 修改 `index.html` 路由 | 新增 `/reader/:paperId` 路由 |
| `vite.config.ts` | 无需改动（CDN 加载 pdf.js） |

## 八、构建输出

依然单一 `app.html`，pdf.js 运行时从 CDN 按需加载。首次打开 reader 页面时额外加载 ~400KB（pdf.js + worker），浏览器缓存后后续秒开。
