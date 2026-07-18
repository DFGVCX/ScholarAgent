from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "deploy" / "Dockerfile.browser"
COMPOSE_FILE = ROOT / "docker-compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"


class BrowserDockerImageTests(unittest.TestCase):
    def test_docker_env_example_uses_compose_service_hosts(self) -> None:
        env_example = ENV_EXAMPLE.read_text(encoding="utf-8")

        self.assertIn("@db:5432/scholar_agent", env_example)
        self.assertIn("SCHOLAR_REDIS_URL=redis://redis:6379/0", env_example)
        self.assertIn("SCHOLAR_FRONTEND_PORT=3000", env_example)

    def test_frontend_host_port_is_configurable_and_avoids_privileged_port(self) -> None:
        compose = COMPOSE_FILE.read_text(encoding="utf-8")

        self.assertIn('${SCHOLAR_FRONTEND_PORT:-3000}:80', compose)

    def test_pip_install_survives_slow_or_interrupted_downloads(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")

        self.assertIn("--mount=type=cache,target=/root/.cache/pip", dockerfile)
        self.assertIn("--timeout 120", dockerfile)
        self.assertIn("--retries 10", dockerfile)
        self.assertNotIn("--no-cache-dir", dockerfile)

    def test_debian_sources_use_https_before_playwright_installs_dependencies(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")

        https_config = "s|http://deb.debian.org|https://deb.debian.org|g"
        install_command = "playwright install --with-deps chromium"
        self.assertIn(https_config, dockerfile)
        self.assertIn('Acquire::Retries "5"', dockerfile)
        self.assertIn(install_command, dockerfile)
        self.assertLess(dockerfile.index(https_config), dockerfile.index(install_command))


if __name__ == "__main__":
    unittest.main()
