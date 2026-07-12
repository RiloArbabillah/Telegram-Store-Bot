import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from handlers.admin_handlers import admin_open_web_panel_callback
from utils.keyboards import create_admin_main_menu_keyboard


class AdminBotLinkTests(unittest.TestCase):
    def test_admin_menu_contains_web_panel_action(self):
        markup = create_admin_main_menu_keyboard()
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        ]
        labels = [
            button.text
            for row in markup.inline_keyboard
            for button in row
        ]

        self.assertIn("admin_open_web_panel", callbacks)
        self.assertIn("🔑 Buat OTP Panel", labels)


class AdminOpenWebPanelCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_receives_otp_without_web_panel_url_button(self):
        query = SimpleNamespace(
            answer=AsyncMock(),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=1778826732),
        )

        with (
            patch("handlers.admin_handlers.is_admin", return_value=True),
            patch("handlers.admin_handlers.create_admin_otp", return_value="12345678") as create_admin_otp,
            patch("handlers.admin_handlers.get_db_session"),
        ):
            await admin_open_web_panel_callback(update, Mock())

        create_admin_otp.assert_called_once()
        query.message.reply_text.assert_awaited_once()
        message = query.message.reply_text.await_args.args[0]
        self.assertIn("12345678", message)
        self.assertIn("5 menit", message)
        self.assertIsNone(query.message.reply_text.await_args.kwargs.get("reply_markup"))

    async def test_non_admin_cannot_request_otp(self):
        query = SimpleNamespace(
            answer=AsyncMock(),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=999),
        )

        with (
            patch("handlers.admin_handlers.is_admin", return_value=False),
            patch("handlers.admin_handlers.create_admin_otp") as create_admin_otp,
        ):
            await admin_open_web_panel_callback(update, Mock())

        create_admin_otp.assert_not_called()
        query.message.reply_text.assert_not_called()
        query.answer.assert_any_await("⛔ Access denied.", show_alert=True)


if __name__ == "__main__":
    unittest.main()
