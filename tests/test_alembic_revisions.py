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
        self.assertEqual(heads, {"20260901_0010"})

    def test_pdf_ingestion_jobs_are_unique_while_active(self) -> None:
        migration = Path("alembic/versions/20260901_0008_pdf_ingestion_queue.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("uq_active_pdf_ingestion_job", migration)
        self.assertIn("job_type='ingest_pdf'", migration)
        self.assertIn("status IN ('pending','running','retry')", migration)

    def test_pdf_ingestion_queue_has_generation_and_fenced_lease(self) -> None:
        migration = Path(
            "alembic/versions/20260901_0009_pdf_ingestion_fencing.py"
        ).read_text(encoding="utf-8")

        self.assertIn('down_revision = "20260901_0008"', migration)
        self.assertIn("ingestion_generation", migration)
        self.assertIn("generation_uuid", migration)
        self.assertIn("asset_sha256", migration)
        self.assertIn("lease_token", migration)
        self.assertIn("DROP INDEX IF EXISTS uq_active_pdf_ingestion_job", migration)
        self.assertIn("uq_waiting_pdf_ingestion_job", migration)
        self.assertIn("status IN ('pending','retry')", migration)
        self.assertIn("ROW_NUMBER() OVER", migration)
        self.assertIn("ranked.active_rank > 1", migration)

    def test_pdf_content_versions_are_idempotent_per_ingestion_generation(self) -> None:
        migration = Path(
            "alembic/versions/20260901_0010_pdf_content_generation.py"
        ).read_text(encoding="utf-8")

        self.assertIn('down_revision = "20260901_0009"', migration)
        self.assertIn("paper_contents ADD COLUMN ingestion_generation", migration)
        self.assertIn("uq_paper_content_ingestion_generation", migration)

    def test_hierarchical_chunk_constraint_accepts_source_code(self) -> None:
        migration = Path("alembic/versions/20260828_0006_code_chunks.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("'algorithm','code'", migration)
        downgrade = migration.split("def downgrade() -> None:", 1)[1]
        self.assertLess(
            downgrade.index("UPDATE paper_chunks SET chunk_type='prose'"),
            downgrade.index("CHECK (chunk_type IN ('prose','equation','table','figure','algorithm'))"),
        )

    def test_embedding_usage_events_are_tenant_scoped_and_indexed(self) -> None:
        migration = Path("alembic/versions/20260901_0007_embedding_usage.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("CREATE TABLE embedding_usage_events", migration)
        self.assertIn("tenant_id TEXT NOT NULL", migration)
        self.assertIn("user_id TEXT NOT NULL", migration)
        self.assertIn("ENABLE ROW LEVEL SECURITY", migration)
        self.assertIn("embedding_usage_events_tenant_user_policy", migration)
        self.assertIn("idx_embedding_usage_scope_created", migration)
        self.assertIn("successful_request_count + failed_request_count = request_count", migration)
        self.assertIn("cancelled_request_count <= failed_request_count", migration)
        self.assertIn("successful_usage_reported_requests <= successful_request_count", migration)


if __name__ == "__main__":
    unittest.main()
