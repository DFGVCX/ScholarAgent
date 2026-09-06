from __future__ import annotations

from pathlib import Path
import unittest


class PostgreSQLOperationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = Path("scripts/postgres_disaster_rehearsal.ps1").read_text(encoding="utf-8")

    def test_rehearsal_uses_custom_backup_and_disposable_restore_database(self) -> None:
        self.assertIn("pg_dump", self.script)
        self.assertIn("--format=custom", self.script)
        self.assertIn("pg_restore", self.script)
        self.assertIn("--exit-on-error", self.script)
        self.assertIn("_restore_check_", self.script)
        self.assertIn("if ($restoreDatabase -eq $sourceDatabase)", self.script)

    def test_rehearsal_checks_migration_rollback_and_returns_to_head(self) -> None:
        self.assertIn("alembic downgrade -1", self.script)
        self.assertIn("alembic upgrade head", self.script)
        self.assertIn("alembic current", self.script)
        self.assertIn("CREATE EXTENSION IF NOT EXISTS vector", self.script)

    def test_rehearsal_never_drops_the_source_database(self) -> None:
        self.assertNotIn('dropdb --if-exists -U $databaseUser $sourceDatabase', self.script)
        self.assertIn('dropdb --if-exists -U $databaseUser $restoreDatabase', self.script)

    def test_rehearsal_rejects_docker_client_without_a_running_server(self) -> None:
        self.assertIn("$dockerServer = docker version", self.script)
        self.assertIn('[string]::IsNullOrWhiteSpace($dockerServer)', self.script)
        self.assertIn('$dockerServer -eq "null"', self.script)


if __name__ == "__main__":
    unittest.main()
