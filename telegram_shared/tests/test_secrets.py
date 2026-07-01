import unittest

from telegram_shared import secrets
from telegram_shared.errors import SecretResolutionError


class SharedSecretsTests(unittest.TestCase):
    def test_resolve_keychain_secret_rejects_invalid_reference_with_typed_error(self) -> None:
        with self.assertRaises(SecretResolutionError) as ctx:
            secrets.resolve_secret_value("keychain://telegram-connector", "Bot token")

        self.assertEqual(
            str(ctx.exception),
            "Invalid Keychain reference for Bot token. Use keychain://<service>/<account>.",
        )


if __name__ == "__main__":
    unittest.main()
