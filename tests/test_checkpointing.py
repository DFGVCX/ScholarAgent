from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agents.checkpointing import CheckpointProvider


class _SaverContext:
    def __init__(self, saver) -> None:
        self.saver = saver
        self.closed = False

    async def __aenter__(self):
        return self.saver

    async def __aexit__(self, *args):
        self.closed = True


class CheckpointingTests(unittest.IsolatedAsyncioTestCase):
    async def test_postgres_checkpointer_is_default_durable_backend(self) -> None:
        provider = CheckpointProvider()
        saver = Mock(setup=AsyncMock())
        context = _SaverContext(saver)
        with patch.dict(
            os.environ,
            {"SCHOLAR_DATABASE_URL": "postgresql+psycopg://u:p@db/scholar"},
            clear=False,
        ), patch(
            "agents.checkpointing.AsyncPostgresSaver.from_conn_string", return_value=context
        ) as factory:
            result = await provider.get()
            await provider.close()

        self.assertIs(result, saver)
        factory.assert_called_once_with("postgresql://u:p@db/scholar")
        saver.setup.assert_awaited_once()
        self.assertTrue(context.closed)


if __name__ == "__main__":
    unittest.main()
