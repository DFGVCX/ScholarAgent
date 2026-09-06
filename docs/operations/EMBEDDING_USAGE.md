# Embedding 用量统计口径

ScholarAgent 从迁移 `20260901_0007` 开始按租户和用户记录千问 Embedding 用量。历史调用不会反推或补写。

## 指标

- 逻辑调用：一次 `embed()`，可能因为批处理和重试产生多个 HTTP 请求。
- API 请求：实际向服务商发出的 HTTP 请求数，包含重试；成功、失败和请求中被取消分别计数，三者不会混淆。
- 逻辑失败率：失败或被超时取消的逻辑调用数 / 全部逻辑调用数。
- API 请求失败率：限流、服务端错误、网络错误和不合规响应请求数 / 全部 API 请求数。
- 厂商回传 Token：仅累加响应 `usage.prompt_tokens`；缺失时回退 `usage.total_tokens`。项目不会使用字符数伪造 Token。
- Token 覆盖缺口：仅统计“向量响应验证成功、但没有可用 `usage`”的请求；超时取消不会被误算为成功。
- 估算费用：厂商回传 Token × 手工配置的人民币单价 / 1,000,000。单价为 0 时显示“未配置单价”。

## 配置

可在网站“设置 → 模型与 RAG”填写“Embedding 单价（元 / 百万 Token）”，或设置：

```env
SCHOLAR_RAG_EMBEDDING_COST_CNY_PER_MILLION_TOKENS=0
```

修改价格只影响统计展示，不会探测模型、标记旧向量或触发重建。

## 数据与隔离

事件表为 `embedding_usage_events`，应用场景标记为 `probe`、`ingestion`、`reindex`、`retrieval` 或 `evaluation`。表启用强制 PostgreSQL RLS；统计接口只返回当前 tenant/user 的累计值。事件只保存错误类型，不保存服务商错误正文、输入文本或 API Key。

厂商只要返回合法 `usage`，即使该 HTTP 响应随后因限流、服务端状态或向量格式错误而失败，Token 仍会计入，避免漏掉可能已经计费的请求。

用量写入使用独立事务，并且只在论文向量/重建任务的主事务提交后执行。等待上限默认 250 ms；超时会取消后台写入并返回主结果。监控失败或卡顿不会回滚已经解析的论文、已生成的向量，也不会替换 lexical 降级结果。
