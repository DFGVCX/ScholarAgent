from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import import_module, invalidate_caches
from pathlib import Path
from threading import RLock
from typing import Any, AsyncIterator, Callable

import yaml


WorkflowCallable = Callable[[dict[str, Any]], AsyncIterator[dict[str, Any]]]
SKILL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
MODULE_PATTERN = re.compile(r"^skills\.[a-zA-Z0-9_.]+$")
ENTRYPOINT_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


@dataclass(frozen=True)
class SkillDescriptor:
    name: str
    module_path: str
    workflow_attr: str = "run_survey_workflow"
    version: str = "0.1.0"
    description: str = ""
    enabled: bool = True
    manifest_path: str = ""


class SkillRegistry:
    """Discover workflow entrypoints from versioned SKILL.md manifests."""

    def __init__(self, root: Path | None = None, *, auto_discover: bool = True) -> None:
        self.root = root or Path(__file__).resolve().parents[1] / "skills"
        self.auto_discover = auto_discover
        self._manual: dict[str, SkillDescriptor] = {}
        self._discovered: dict[str, SkillDescriptor] = {}
        self._fingerprint: tuple[tuple[str, int, int], ...] = ()
        self._lock = RLock()
        if auto_discover:
            self.refresh(force=True)

    def register(self, descriptor: SkillDescriptor) -> None:
        self._validate(descriptor)
        with self._lock:
            self._manual[descriptor.name] = descriptor

    def refresh(self, *, force: bool = False) -> dict[str, list[str]]:
        manifests = (
            sorted(
                path for path in self.root.glob("*/SKILL.md")
                if not path.parent.name.startswith("_")
            )
            if self.root.exists()
            else []
        )
        fingerprint = tuple(
            (str(path.resolve()), path.stat().st_mtime_ns, path.stat().st_size)
            for path in manifests
        )
        with self._lock:
            if not force and fingerprint == self._fingerprint:
                return {"added": [], "updated": [], "removed": []}
            previous = self._discovered
            discovered: dict[str, SkillDescriptor] = {}
            for manifest in manifests:
                descriptor = self._load_manifest(manifest)
                if descriptor is not None:
                    if descriptor.name in discovered:
                        raise ValueError(f"Duplicate skill name in manifests: {descriptor.name}")
                    discovered[descriptor.name] = descriptor
            self._discovered = discovered
            self._fingerprint = fingerprint
            invalidate_caches()
            return {
                "added": sorted(discovered.keys() - previous.keys()),
                "updated": sorted(
                    name for name in discovered.keys() & previous.keys()
                    if discovered[name] != previous[name]
                ),
                "removed": sorted(previous.keys() - discovered.keys()),
            }

    def _refresh_if_changed(self) -> None:
        if self.auto_discover:
            self.refresh()

    def _all(self) -> dict[str, SkillDescriptor]:
        return self._discovered | self._manual

    def has_skill(self, name: str) -> bool:
        self._refresh_if_changed()
        return name in self._all()

    def list_skills(self) -> list[SkillDescriptor]:
        self._refresh_if_changed()
        return sorted(self._all().values(), key=lambda descriptor: descriptor.name)

    def get_workflow(self, name: str) -> WorkflowCallable:
        self._refresh_if_changed()
        skills = self._all()
        if name not in skills:
            available = ", ".join(sorted(skills)) or "none"
            raise KeyError(f"Unknown skill '{name}'. Available skills: {available}")
        descriptor = skills[name]
        module = import_module(descriptor.module_path)
        workflow = getattr(module, descriptor.workflow_attr, None)
        if not callable(workflow):
            raise TypeError(
                f"Skill '{name}' entrypoint {descriptor.module_path}:"
                f"{descriptor.workflow_attr} is not callable"
            )
        return workflow

    @classmethod
    def _load_manifest(cls, path: Path) -> SkillDescriptor | None:
        content = path.read_text(encoding="utf-8")
        frontmatter = re.findall(
            r"(?:^|\n)---\r?\n(.*?)\r?\n---(?:\r?\n|$)",
            content,
            flags=re.DOTALL,
        )
        if not frontmatter:
            return None
        metadata = yaml.safe_load(frontmatter[-1]) or {}
        if not isinstance(metadata, dict):
            raise ValueError(f"SKILL.md frontmatter must be an object: {path}")
        descriptor = SkillDescriptor(
            name=str(metadata.get("name") or "").strip(),
            module_path=str(metadata.get("module") or "").strip(),
            workflow_attr=str(metadata.get("entrypoint") or "run_survey_workflow").strip(),
            version=str(metadata.get("version") or "0.1.0").strip(),
            description=str(metadata.get("description") or "").strip(),
            enabled=bool(metadata.get("enabled", True)),
            manifest_path=str(path.resolve()),
        )
        cls._validate(descriptor)
        return descriptor if descriptor.enabled else None

    @staticmethod
    def _validate(descriptor: SkillDescriptor) -> None:
        if not SKILL_NAME_PATTERN.fullmatch(descriptor.name):
            raise ValueError(f"Invalid skill name: {descriptor.name!r}")
        if not MODULE_PATTERN.fullmatch(descriptor.module_path):
            raise ValueError(
                f"Skill module must stay under the skills package: {descriptor.module_path!r}"
            )
        if not ENTRYPOINT_PATTERN.fullmatch(descriptor.workflow_attr):
            raise ValueError(f"Invalid skill entrypoint: {descriptor.workflow_attr!r}")


skill_registry = SkillRegistry()
