from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, AsyncIterator, Callable


WorkflowCallable = Callable[[dict[str, Any]], AsyncIterator[dict[str, Any]]]


@dataclass(frozen=True)
class SkillDescriptor:
    name: str
    module_path: str
    workflow_attr: str = "run_survey_workflow"


class SkillRegistry:
    def __init__(self) -> None:
        self._skills = {
            "survey_generation": SkillDescriptor(
                name="survey_generation",
                module_path="skills.survey_generation.main_workflow",
            )
        }

    def register(self, descriptor: SkillDescriptor) -> None:
        self._skills[descriptor.name] = descriptor

    def has_skill(self, name: str) -> bool:
        return name in self._skills

    def list_skills(self) -> list[SkillDescriptor]:
        return list(self._skills.values())

    def get_workflow(self, name: str) -> WorkflowCallable:
        if name not in self._skills:
            available = ", ".join(sorted(self._skills)) or "none"
            raise KeyError(f"Unknown skill '{name}'. Available skills: {available}")
        descriptor = self._skills[name]
        module = import_module(descriptor.module_path)
        return getattr(module, descriptor.workflow_attr)


skill_registry = SkillRegistry()
