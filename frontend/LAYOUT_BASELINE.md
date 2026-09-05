# 前端布局基线

当前稳定版布局由 `frontend/public/assets/layout-baseline.v1.css` 统一维护，构建后复制到 `frontend/dist/assets/`。

## 约束

- `app.html` 中的功能样式可以继续演进，但不得新增影响多个页面的结构性裸选择器。
- 页面布局必须以 `#pageChat`、`#pageTasks`、`#pageAudit`、`#pageKnowledge`、`#pageProfile` 或 `#pageReader` 开头。
- 基线文件只管理网格、尺寸、滚动与溢出，不管理颜色、文案和业务状态。
- 前四个工作模块共用同一左栏：最大 `302px`，模块间距 `14px`。
- 需要整体改版时新增 `layout-baseline.v2.css`，同步修改 `body[data-layout-baseline]`，不要直接覆盖 v1。

## 验证

```powershell
cd frontend
npm run build
cd ..
.\.venv\Scripts\python.exe -m unittest tests.test_frontend_layout_baseline tests.test_frontend_lifecycle_ui -v
```

桌面视觉验收使用 `1280x720` 和 `1900x860` 两档视口，检查各模块左栏宽度、内部滚动和首屏溢出。
