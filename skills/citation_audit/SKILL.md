---
name: citation_audit
version: 1.0.0
description: 检查正文来源 ID、未引用段落及证据定位，输出结构化审计结果。
module: skills.citation_audit.main_workflow
entrypoint: run_citation_audit_workflow
enabled: true
---

# 引用审计

输入：tenant_id、user_id、text、papers。来源必须属于当前用户。
输出：citation_audit，包括 ID 校验和段落证据定位。词面定位不代表语义蕴含。
