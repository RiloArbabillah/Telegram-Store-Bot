import unittest
from unittest.mock import Mock, patch

from handlers.user_handlers import (
    _format_deactivated_result,
    _mailbox_mode_config,
    _single_account_keyboard,
)
from services.mailbox import fetch_mailbox_messages, summarize_deactivated_messages


class DeactivatedMailboxTests(unittest.TestCase):
    def test_fetch_uses_explicit_deactivated_keyword(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True, "messages": []}

        with patch("services.mailbox.requests.post", return_value=response) as post:
            fetch_mailbox_messages(
                "buyer@example.com----secret",
                keyword="deactivated",
            )

        self.assertEqual(post.call_args.kwargs["json"]["keyword"], "deactivated")

    def test_detected_result_contains_safe_message_summary(self):
        mailbox_data = {
            "email": "buyer@example.com",
            "count": 1,
            "total": 1,
            "messages": [{
                "from": "OpenAI <noreply@openai.com>",
                "subject": "Your OpenAI account has been deactivated",
                "date": "2026-07-10",
                "body": "private body must not be rendered",
            }],
        }

        result = _format_deactivated_result(mailbox_data)

        self.assertIn("DEACTIVATED terdeteksi", result)
        self.assertIn("Your OpenAI account has been deactivated", result)
        self.assertNotIn("private body", result)

    def test_empty_result_reports_not_found(self):
        result = _format_deactivated_result({
            "email": "buyer@example.com",
            "count": 0,
            "total": 0,
            "messages": [],
        })

        self.assertIn("Email deactivation tidak ditemukan", result)

    def test_summary_limits_messages_and_omits_body(self):
        messages = [
            {"from": "sender", "subject": f"Notice {index}", "date": "today", "body": "secret"}
            for index in range(12)
        ]

        summaries = summarize_deactivated_messages({"messages": messages}, limit=10)

        self.assertEqual(len(summaries), 10)
        self.assertTrue(all("secret" not in summary for summary in summaries))

    def test_deactivated_navigation_keeps_mode(self):
        config = _mailbox_mode_config("deactivated")
        keyboard = _single_account_keyboard(12, 34, mode="deactivated")

        self.assertEqual(config["keyword"], "deactivated")
        self.assertEqual(keyboard.inline_keyboard[0][0].callback_data, "check_deactivated_account_12")
        self.assertEqual(keyboard.inline_keyboard[1][0].callback_data, "check_deactivated_order_34")


if __name__ == "__main__":
    unittest.main()
