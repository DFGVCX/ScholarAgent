from pathlib import Path
import unittest


class FrontendGatewayTests(unittest.TestCase):
    def test_all_console_api_namespaces_are_proxied(self) -> None:
        nginx = Path("deploy/nginx.conf").read_text(encoding="utf-8")

        for prefix in (
            "auth",
            "settings",
            "agents",
            "conversations",
            "tasks",
            "knowledge",
            "institutional-access",
            "health",
        ):
            self.assertIn(prefix, nginx)

        self.assertIn("proxy_pass http://backend:8000", nginx)


if __name__ == "__main__":
    unittest.main()
