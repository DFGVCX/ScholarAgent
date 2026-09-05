from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from desktop.launcher import _configure_environment


class DesktopReleaseTest(unittest.TestCase):
    def test_desktop_environment_uses_user_data_and_no_external_dependencies(self):
        directory = r"C:\PortableUserData"
        with patch.dict(os.environ, {"LOCALAPPDATA": directory}, clear=True), patch(
            "desktop.launcher.Path.mkdir"
        ):
            _configure_environment(18000, 18002)
            self.assertTrue(
                os.environ["SCHOLAR_STORAGE_DIR"].startswith(
                    str(directory + r"\ScholarAgent")
                )
            )
            self.assertEqual(os.environ["SCHOLAR_TASK_EXECUTION_MODE"], "inline")
            self.assertEqual(os.environ["SCHOLAR_REDIS_URL"], "")
            self.assertEqual(os.environ["SCHOLAR_MCP_URL"], "")
            self.assertEqual(os.environ["SCHOLAR_DISABLE_DEMO_USERS"], "true")
            self.assertNotIn("XIA", os.environ["SCHOLAR_STORAGE_DIR"])


if __name__ == "__main__":
    unittest.main()
