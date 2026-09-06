---
name: paper_retrieval
version: 1.0.0
description: 按主题检索本地或联网论文，返回去重候选、来源状态与证据片段。
module: skills.paper_retrieval.main_workflow
entrypoint: run_paper_retrieval_workflow
enabled: true
---

# 论文检索

输入：tenant_id、user_id、query（或 topic）、source（all/local/external）、limit。
输出：items、local_hits、external_candidates、source_status。检索不自动写入知识库。
