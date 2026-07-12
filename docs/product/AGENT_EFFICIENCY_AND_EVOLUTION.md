# Agent 调用提优与受控自进化

## 1. 目标

在不降低语义理解、工具安全和任务成功率的前提下，减少重复模型调用和无效上下文 Token，并把稳定成功的操作轨迹沉淀为可审核 Skill 候选。

本方案采用四层执行顺序：

1. **确定性控制层**：确认、取消、精确状态查询和稳定工具命令不调用模型。
2. **语义规划层**：只有含糊、多约束或依赖历史状态的请求才调用意图规划模型。
3. **受预算生成层**：按照规划、会话、写作、审查等用途设置不同输入输出上限。
4. **受控进化层**：统计成功轨迹，生成禁用状态的 Skill 草稿，人工审核后导出，不自动进入生产注册表。

## 2. Token 优化链路

### 2.1 自适应上下文预算

`agents/context/manager.py` 根据最新用户请求分级：

| 请求类型 | 默认上下文预算 |
|---|---:|
| 普通短对话 | 2,600 tokens |
| 检索、下载、知识库、写作动作 | 4,200 tokens |
| 长文本、多约束、系统分析 | 7,000 tokens |
| 操作回顾 | 3,000 tokens |

上下文只保留相关长期记忆、最近工具证据和必要工作状态。`current_goal` 等已在最新消息出现的字段不再重复写入 Prompt。

### 2.2 用途级模型预算

`agents/runtime/token_policy.py` 在请求进入模型供应商前执行硬限制：

| Purpose | 输入上限 | 输出上限 | 精确缓存 |
|---|---:|---:|---:|
| intent_planning | 1,800 | 420 | 5 分钟 |
| conversation | 5,600 | 1,000 | 不缓存 |
| orchestrator_synthesis | 7,000 | 1,200 | 不缓存 |
| outline | 5,200 | 900 | 30 分钟 |
| section | 6,000 | 1,200 | 60 分钟 |
| critic | 3,600 | 600 | 30 分钟 |

缓存键包含租户、用户、provider、purpose、Prompt 和压缩后的上下文，禁止跨租户复用。

### 2.3 Tool Loop 降耗

- MCP 工具目录缓存 60 秒，避免每轮重复发现。
- 意图规划只携带工具名称、说明、参数名和必填字段，不传完整 JSON Schema。
- 精确机构状态、知识库列表和 Paper ID 查询直接走确定性路由。
- 复杂或上下文相关的研究主题仍交给模型规划，避免规则清洗误伤主题。

### 2.4 可观测指标

每次模型调用记录：

- `purpose`
- `input_tokens`
- `output_tokens`
- `total_tokens`
- `cached`
- provider、model、latency、success

会话响应 metadata 同步返回本次 usage 与上下文预算，便于前端或 Langfuse 展示。

## 3. 受控自进化

### 3.1 轨迹沉淀

Tool Loop 完成后，把脱敏后的操作骨架写入 `scholar_operation_patterns`。查询内容、论文 ID 和 URL 转成变量占位符，API Key、Token、租户和用户字段不会进入候选。

默认满足以下条件才生成候选：

- 同一操作骨架成功至少 3 次；
- 成功率至少 80%；
- 候选属于当前 tenant/user；
- 候选状态固定为 `draft`。

### 3.2 审核与导出

接口：

- `GET /agents/skill-candidates?status=draft`
- `POST /agents/skill-candidates/{candidate_id}/review`

审核通过后，系统在 `storage/runtime/skill_candidates/<tenant>/<candidate>/SKILL.md` 导出草稿。该文件保持 `enabled: false`，不会被生产 `SkillRegistry` 自动加载。

正式发布仍需：

1. 明确输入输出契约和安全级别；
2. 补充 workflow 或确定性脚本；
3. 增加单元测试和端到端测试；
4. 由开发者迁移至 `skills/<skill_name>/`；
5. 经 `SKILL.md` 热发现机制加载。

## 4. 安全边界

- 不让模型自行修改代码、Prompt、权限或生产 Skill。
- 不根据单次成功生成 Skill。
- 失败、取消和拒绝操作都会进入成功率统计。
- 删除、下载、批量写入等副作用仍经过原有确认机制。
- 模型缓存只存进程内短时结果，且按租户和用户隔离。

## 5. 预期收益与验证方法

本实现不预设虚假节省比例。应通过 `scholar_trace_events.metadata_json` 的真实数据计算：

```text
Token 节省率 = 1 - 优化后总 input_tokens / 基线总 input_tokens
规划免调用率 = 确定性规划次数 / 全部规划请求数
缓存命中率 = cached=true 的调用数 / 可缓存调用数
Skill 候选有效率 = 审核通过候选数 / 候选总数
```

建议使用同一组 100 条会话与 30 条写作任务做优化前后 A/B 回放，并同时比较任务成功率、工具选择准确率、平均 Token 和 P95 延迟。
