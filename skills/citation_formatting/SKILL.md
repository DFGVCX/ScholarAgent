---
name: citation_formatting
version: 1.0.0
description: 将已核验的论文元数据转换为 IEEE、APA、GB/T 7714 参考文献。
module: skills.citation_formatting.main_workflow
entrypoint: run_citation_formatting_workflow
enabled: true
---

# 引用格式转换

输入：tenant_id、user_id、papers、citation_style。
输出：references、formatter_status。沿用现有格式化接口，明确标记规则/适配器实际模式。本轮不包含 LoRA 训练。
