from __future__ import annotations

import unittest

from app.config import Settings, validate_release_settings


class ReleaseConfigTest(unittest.TestCase):
    def test_production_rejects_development_defaults(self):
        with self.assertRaises(RuntimeError):
            validate_release_settings(Settings(env="production"))

    def test_production_accepts_explicit_security_settings(self):
        validate_release_settings(
            Settings(
                env="production",
                api_keys="random-production-key:tenant:user",
                allow_mock_data=False,
                cors_allow_origins=("https://scholar.example.com",),
            )
        )


if __name__ == "__main__":
    unittest.main()
