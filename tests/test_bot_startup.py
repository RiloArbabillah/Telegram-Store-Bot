import logging
import os
import unittest
from unittest.mock import Mock, patch

from telegram.error import TimedOut

from bot import initialize_database_for_process, run_polling_with_startup_retry


class BotStartupTests(unittest.TestCase):
    def test_http_client_does_not_log_telegram_token_urls_at_info(self):
        self.assertGreater(logging.getLogger("httpx").getEffectiveLevel(), logging.INFO)

    @patch("bot.initialize_database")
    def test_skips_database_initialization_when_launcher_already_prepared_it(self, initialize):
        with patch.dict(os.environ, {"DATABASE_INITIALIZED": "1"}):
            initialize_database_for_process()

        initialize.assert_not_called()

    @patch("bot.time.sleep")
    def test_retries_startup_timeout_without_exiting(self, sleep):
        application = Mock()
        application.run_polling.side_effect = [TimedOut(), None]

        run_polling_with_startup_retry(application)

        self.assertEqual(application.run_polling.call_count, 2)
        application.run_polling.assert_called_with(
            allowed_updates=["message", "callback_query", "pre_checkout_query"],
            close_loop=False,
        )
        sleep.assert_called_once_with(5)


if __name__ == "__main__":
    unittest.main()
