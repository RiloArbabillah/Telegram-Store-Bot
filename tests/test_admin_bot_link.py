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

        self.assertIn("admin_open_web_panel", callbacks)


class AdminOpenWebPanelCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_localhost_webhook_url_before_creating_login_token(self):
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
            patch.object(
                admin_open_web_panel_callback.__globals__["app_settings"],
                "WEBHOOK_BASE_URL",
                "https://localhost:3000",
            ),
            patch("handlers.admin_handlers.create_login_token") as create_login_token,
            patch("handlers.admin_handlers.get_db_session"),
        ):
            await admin_open_web_panel_callback(update, Mock())

        create_login_token.assert_not_called()
        query.message.reply_text.assert_not_called()
        query.answer.assert_any_await(
            "Domain panel harus memakai URL HTTPS publik, bukan localhost.",
            show_alert=True,
        )


if __name__ == "__main__":
    unittest.main()
