# ScholarAgent 上下文与调度架构

## 目标

ScholarAgent 不再通过“最近若干条消息 + 关键词”推断任务状态，而是采用四层上下文：

1. 稳定系统层：租户安全规则、工具契约、Agent 职责和项目约束。
2. 工作状态层：当前目标、阶段、数据源、待确认动作、产物和失败恢复点。
3. 会话层：受保护首部、中段压缩摘要、最近消息和真实工具账本。
4. 长期记忆层：用户偏好、研究方向、引用风格和长期约束。

显式用户指令优先级始终高于工作状态、会话摘要和长期记忆。

## 每轮生命周期

```text
用户消息
  -> 写入会话
  -> 更新 ConversationWorkingState(current_goal, phase=planning)
  -> 构建预算内上下文
  -> 确定性工具规划 / Agent 路由
  -> 保存路由理由与计划
  -> 执行工具或 Agent
  -> 将结果归约回工作状态
  -> 保存助手消息
```

工具执行状态不从自然语言回复推断，而是从工具调用记录归约。进程重启后，可以通过数据库中的工作状态、工具账本和会话消息恢复。

## ConversationWorkingState

核心字段：

- `state_version`：每次状态变更递增，用于追踪并发和恢复。
- `current_goal`：最新明确目标。
- `active_domain`：当前工作域，例如 `literature`。
- `active_source`：当前数据源，例如 `cnki`、`local`、`all`。
- `phase`：`planning`、`selection_ready`、`awaiting_confirmation`、`completed` 等。
- `pending_action`：等待用户确认的真实工具调用。
- `last_route`：意图、目标、执行模式、理由、置信度和计划步骤。
- `artifacts`：已生成或入库的论文等产物引用。
- `last_error`：最近一次真实失败，用于恢复决策。

状态存储按 `tenant_id + user_id + conversation_id` 隔离。

## 路由策略

路由结果统一输出：

```json
{
  "intent": "academic_writing",
  "target": "writing_agent",
  "execution_mode": "skill|tool|tool_pipeline|delegation|direct",
  "reasons": ["writing_intent", "multi_stage_reasoning"],
  "confidence": 0.96,
  "planned_steps": ["clarify_scope", "route_writing_skill", "review_output"]
}
```

简单领域任务优先执行单 Skill；包含多个约束、显式步骤或多阶段推理时，升级为领域 Agent 或受限子 Agent 协作。下载、删除等有副作用操作仍必须进入确认状态。

## 上下文压缩

- 首部用于保留原始目标与关键约束。
- 中段只保留去重后的事实摘要，不递归累加完整摘要。
- 尾部保留最近消息。
- 工具结果只注入精简引用，不注入大段正文或原始响应。
- 达到硬 Token 预算时，优先保留当前状态和最近用户消息。

## 记忆治理

长期记忆只保存可复用信息：用户偏好、项目事实、稳定约束和明确要求。临时文件、原始日志、大段论文正文和可重新查询的信息不进入长期记忆。记忆支持查看、写入和遗忘。

## 设计依据

- Hermes Agent 使用受限持久记忆、每轮预取、会话同步、压缩前提取，以及由 Agent Loop 直接处理 Todo、Memory、Session Search 和 Delegation。
- Hermes Agent Loop 在每轮执行预检压缩、临时压力提示、工具调用循环和会话持久化。
- Coding Agent 的稳定前缀与变化状态应分层，避免频繁重建系统提示并破坏缓存。

参考：

- https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/agent-loop.md
- https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory.md
- https://github.com/NousResearch/hermes-agent/blob/main/agent/memory_provider.py
