from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from psycopg import OperationalError

from app.services import mysql_store


class PostgreSQLStoreTest(unittest.TestCase):
    def tearDown(self) -> None:
        mysql_store.reset_availability_cache()

    def test_qmark_placeholders_are_converted_without_touching_literals(self) -> None:
        translated = mysql_store._translate_sql(
            "SELECT '?' AS literal, \"?\" AS identifier FROM papers WHERE a = ? AND b = %s"
        )
        self.assertEqual(
            translated,
            "SELECT '?' AS literal, \"?\" AS identifier FROM papers WHERE a = %s AND b = %s",
        )

    def test_sqlite_datetime_expression_is_converted(self) -> None:
        self.assertEqual(
            mysql_store._translate_sql("UPDATE papers SET updated_at = datetime('now') WHERE id = ?"),
            "UPDATE papers SET updated_at = CURRENT_TIMESTAMP WHERE id = %s",
        )

    def test_unreachable_database_is_not_available(self) -> None:
        class BrokenPool:
            @contextmanager
            def connection(self):
                raise OperationalError("down")
                yield

        with patch.object(mysql_store, "_get_pool", return_value=BrokenPool()):
            mysql_store.reset_availability_cache()
            self.assertFalse(mysql_store.is_available())

    def test_database_name_comes_from_url(self) -> None:
        with patch.dict(
            os.environ,
            {"SCHOLAR_DATABASE_URL": "postgresql+psycopg://scholar:secret@db:5432/research_db"},
            clear=False,
        ):
            self.assertEqual(mysql_store.configured_database_name(), "research_db")


if __name__ == "__main__":
    unittest.main()
