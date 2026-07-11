import unittest

import webhook_server


class AdminPanelRegistrationTests(unittest.TestCase):
    def test_web_app_registers_admin_and_preserves_webhooks(self):
        rules = {rule.rule for rule in webhook_server.app.url_map.iter_rules()}

        self.assertIn("/admin", rules)
        self.assertIn("/admin/login", rules)
        self.assertIn("/webhook/cryptobot", rules)
        self.assertIn("/webhook/dana", rules)
        self.assertIn("/webhook/payment-deka", rules)
        self.assertIn("/health", rules)


if __name__ == "__main__":
    unittest.main()
