from __future__ import annotations

import unittest
from types import SimpleNamespace

from agents.skill_registry import SkillRegistry


class FakeManifest:
    def __init__(self, content: str) -> None:
        self.content = content
        self.modified = 1
        self.parent = SimpleNamespace(name="example_skill")

    def resolve(self) -> str:
        return "/skills/example_skill/SKILL.md"

    def stat(self) -> SimpleNamespace:
        return SimpleNamespace(st_mtime_ns=self.modified, st_size=len(self.content))

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.content


class FakeRoot:
    def __init__(self, manifests: list[FakeManifest]) -> None:
        self.manifests = manifests

    def exists(self) -> bool:
        return True

    def glob(self, pattern: str) -> list[FakeManifest]:
        return self.manifests


class SkillRegistryTest(unittest.TestCase):
    def test_manifest_is_discovered_and_updated_without_restart(self) -> None:
        manifest = FakeManifest(
            "---\nname: example_skill\nversion: 1.0.0\n"
            "module: skills.survey_generation.main_workflow\n"
            "entrypoint: run_survey_workflow\nenabled: true\n---\n"
        )
        root = FakeRoot([manifest])
        registry = SkillRegistry(root)  # type: ignore[arg-type]
        self.assertTrue(registry.has_skill("example_skill"))
        self.assertEqual(registry.list_skills()[0].version, "1.0.0")

        manifest.content = manifest.content.replace("1.0.0", "1.1.0")
        manifest.modified += 1
        self.assertEqual(registry.list_skills()[0].version, "1.1.0")

        root.manifests = []
        self.assertFalse(registry.has_skill("example_skill"))

    def test_manifest_cannot_load_modules_outside_skills_package(self) -> None:
        manifest = FakeManifest(
            "---\nname: unsafe_skill\nmodule: os\nentrypoint: system\n---\n"
        )
        with self.assertRaises(ValueError):
            SkillRegistry(FakeRoot([manifest]))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
