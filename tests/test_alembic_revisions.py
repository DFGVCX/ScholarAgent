from __future__ import annotations

import ast
from pathlib import Path
import unittest


class AlembicRevisionGraphTest(unittest.TestCase):
    def test_revision_ids_are_unique_and_graph_has_one_head(self) -> None:
        revisions: dict[str, str | None] = {}
        seen_files: dict[str, Path] = {}
        for path in sorted(Path("alembic/versions").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            values: dict[str, str | None] = {}
            for node in tree.body:
                if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                    continue
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                    values[target.id] = ast.literal_eval(node.value)
            revision = values.get("revision")
            self.assertIsNotNone(revision, path)
            self.assertNotIn(revision, seen_files, f"duplicate revision {revision}: {seen_files.get(revision)} and {path}")
            seen_files[str(revision)] = path
            revisions[str(revision)] = values.get("down_revision")

        parents = {parent for parent in revisions.values() if parent is not None}
        heads = set(revisions) - parents
        self.assertEqual(heads, {"20260717_0004"})


if __name__ == "__main__":
    unittest.main()
