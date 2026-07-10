import unittest
from unittest.mock import Mock, patch

from telegram.error import TimedOut

from bot import run_polling_with_startup_retry


class BotStartupTests(unittest.TestCase):
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
