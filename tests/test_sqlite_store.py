from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from app.services import mysql_store


class SqliteStoreTest(unittest.TestCase):
    def test_execute_commits_write(self) -> None:
        connection = Mock()
        connection.execute.return_value.rowcount = 1

        with patch.object(mysql_store, "_get_conn", return_value=connection):
            affected = mysql_store.execute(
                "UPDATE scholar_tasks SET status=? WHERE task_id=?",
                ("completed", "task-1"),
            )

        self.assertEqual(affected, 1)
        connection.commit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
