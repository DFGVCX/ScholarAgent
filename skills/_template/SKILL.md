---
name: skill-template
description: Template for a ScholarAgent atomic skill.
---

# Skill Template

## Responsibility

Describe the capability boundary in one paragraph. A skill should own one coherent atomic capability.

## Inputs

- `task_id`
- `tenant_id`
- `user_id`
- capability-specific fields

## Outputs

The final `skill_result` payload must include structured data that can be rendered, audited, and persisted.

## Events

Use the shared event contract documented in `docs/EXTENSION_CONTRACT.md`.

