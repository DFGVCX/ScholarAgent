from __future__ import annotations

import asyncio
import sys
import unittest
import warnings

from app.db import session as _database_session  # noqa: F401
from app.asyncio_compat import new_psycopg_compatible_event_loop


@unittest.skipUnless(sys.platform == "win32", "Windows-specific psycopg compatibility")
class DatabaseEventLoopTest(unittest.TestCase):
    def test_database_import_selects_psycopg_compatible_event_loop_policy(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy")
            current_policy = asyncio.get_event_loop_policy()

        self.assertIsInstance(current_policy, selector_policy)

    def test_explicit_uvicorn_loop_factory_returns_selector_loop(self) -> None:
        loop = new_psycopg_compatible_event_loop()
        try:
            self.assertIsInstance(loop, asyncio.SelectorEventLoop)
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
