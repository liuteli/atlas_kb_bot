import unittest

from app.bot.telegram_bot import TelegramReviewBot


class TelegramBotTests(unittest.TestCase):
    def test_registered_commands_include_whoami(self):
        self.assertIn("/whoami", TelegramReviewBot.registered_commands())

    def test_whoami_text(self):
        text = TelegramReviewBot.whoami_text({
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 456, "username": "vincent"},
        })
        self.assertIn("chat_id: 123", text)
        self.assertIn("user_id: 456", text)
        self.assertIn("username: vincent", text)


if __name__ == "__main__":
    unittest.main()
