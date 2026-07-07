# Atomic Skills

`skills/` contains independent capabilities. Each skill should be usable by the global agent without leaking UI or API route concerns into the skill code.

Active skills:

- `survey_generation/`: academic survey writing with paper search, outline confirmation, citation audit, and final markdown output.
- `_template/`: copy-only scaffold for new atomic skills; do not register it directly.

Required layout for new skills:

```text
skills/<skill_name>/
├── __init__.py
├── SKILL.md
├── main_workflow.py
├── state.py
└── tools/
    ├── __init__.py
    └── <tool>.py
```

Integration checklist:

- Copy `skills/_template/` to `skills/<skill_name>/`.
- Implement an async workflow that yields progress events and one `skill_result`.
- Keep tool classes small and testable.
- Register the skill in `agents/skill_registry.py`.
- Add tests for the workflow and any citation/RAG/tool behavior.
- Update `docs/EXTENSION_CONTRACT.md` if the event contract changes.
