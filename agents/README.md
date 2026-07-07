# Agent Orchestration

`agents/` coordinates skills. It is intentionally small: route the task, call the selected skill, evaluate the result, and return structured events.

```text
agents/
├── graph.py             # Global workflow and event stream
├── skill_registry.py    # Skill descriptor registry
├── factory.py           # LLM/model provider factory
├── evaluator.py         # Cross-skill result checks
└── state.py             # Shared workflow state typing
```

Rules:

- Keep skill-specific writing/retrieval logic inside `skills/<skill_name>/`.
- Keep MCP/external source implementation inside `mcp_server/`.
- Register every new skill explicitly in `skill_registry.py`.
- Global orchestration should remain deterministic and easy to test.

