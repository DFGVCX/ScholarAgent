import unittest

from app.dependencies import AuthError, authenticate_api_key


class AuthTest(unittest.TestCase):
    def test_demo_key_maps_to_tenant_user(self):
        user = authenticate_api_key("demo-key")

        self.assertEqual(user.tenant_id, "tenant_demo")
        self.assertEqual(user.user_id, "user_demo")

    def test_invalid_key_fails(self):
        with self.assertRaises(AuthError):
            authenticate_api_key("bad-key")


if __name__ == "__main__":
    unittest.main()

