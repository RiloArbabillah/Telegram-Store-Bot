import unittest

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


if __name__ == "__main__":
    unittest.main()
