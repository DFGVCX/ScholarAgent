from __future__ import annotations

import asyncio
import sys
import unittest
import warnings

from app.db import session as _database_session  # noqa: F401


@unittest.skipUnless(sys.platform == "win32", "Windows-specific psycopg compatibility")
class DatabaseEventLoopTest(unittest.TestCase):
    def test_database_import_selects_psycopg_compatible_event_loop_policy(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy")
            current_policy = asyncio.get_event_loop_policy()

        self.assertIsInstance(current_policy, selector_policy)


if __name__ == "__main__":
    unittest.main()
