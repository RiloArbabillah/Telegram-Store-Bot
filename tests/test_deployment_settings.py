import importlib
import os
import unittest
from unittest.mock import patch

settings_module = importlib.import_module("config.settings")


class DeploymentSettingsTests(unittest.TestCase):
    def tearDown(self):
        importlib.reload(settings_module)

    def load_settings(self, **environment):
        with patch.dict(os.environ, environment, clear=False):
            module = importlib.reload(settings_module)
            return module.Settings(), module

    def test_normalizes_webhook_base_url_and_builds_callback_url(self):
        settings, _ = self.load_settings(
            WEBHOOK_BASE_URL="https://bot.example.com/",
            PORT="8080",
        )

        self.assertEqual(settings.WEBHOOK_BASE_URL, "https://bot.example.com")
        self.assertEqual(settings.PORT, 8080)
        self.assertEqual(
            settings.callback_url("/webhook/dana"),
            "https://bot.example.com/webhook/dana",
        )

    def test_normalizes_coolify_postgres_url_for_psycopg(self):
        settings, _ = self.load_settings(
            DATABASE_URL="postgres://store:secret@postgres:5432/store?sslmode=require",
        )

        self.assertEqual(
            settings.DATABASE_URL,
            "postgresql+psycopg://store:secret@postgres:5432/store?sslmode=require",
        )

    def test_normalizes_generic_postgresql_url_for_psycopg(self):
        settings, _ = self.load_settings(
            DATABASE_URL="postgresql://store:secret@postgres:5432/store",
        )

        self.assertEqual(
            settings.DATABASE_URL,
            "postgresql+psycopg://store:secret@postgres:5432/store",
        )

    def test_invalid_port_uses_default(self):
        settings, _ = self.load_settings(PORT="not-a-number")

        self.assertEqual(settings.PORT, 3000)

    def test_callback_url_returns_path_without_public_domain(self):
        settings, _ = self.load_settings(WEBHOOK_BASE_URL="")

        self.assertEqual(settings.callback_url("webhook/cryptobot"), "/webhook/cryptobot")

    def test_validation_rejects_non_https_webhook_domain(self):
        _, module = self.load_settings(
            BOT_TOKEN="test-token",
            ADMIN_TELEGRAM_ID="123",
            WEBHOOK_BASE_URL="http://bot.example.com",
        )

        with self.assertRaisesRegex(ValueError, "WEBHOOK_BASE_URL must use https://"):
            module.validate_settings()

    def test_admin_security_settings_are_loaded(self):
        settings, _ = self.load_settings(
            ADMIN_SESSION_SECRET="x" * 32,
            ADMIN_COOKIE_SECURE="false",
        )

        self.assertEqual(settings.ADMIN_SESSION_SECRET, "x" * 32)
        self.assertFalse(settings.ADMIN_COOKIE_SECURE)

    def test_validation_requires_strong_admin_session_secret(self):
        _, module = self.load_settings(
            BOT_TOKEN="test-token",
            ADMIN_TELEGRAM_ID="123",
            WEBHOOK_BASE_URL="https://bot.example.com",
            ADMIN_SESSION_SECRET="too-short",
        )

        with self.assertRaisesRegex(ValueError, "ADMIN_SESSION_SECRET"):
            module.validate_settings()


if __name__ == "__main__":
    unittest.main()
