# Skill Template

Copy this folder to `skills/<skill_name>/`, rename workflow functions, and register the skill in `agents/skill_registry.py`.

Do not import FastAPI request objects in a skill. Receive a structured `initial_state`, yield structured events, and return one `skill_result`.

