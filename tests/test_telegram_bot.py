import unittest
from types import SimpleNamespace

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

    def test_sources_text_uses_short_id_and_review_hint(self):
        bot = object.__new__(TelegramReviewBot)
        record = SimpleNamespace(
            source_id="chatgpt_123456789abc",
            display_name="2026-04-12_185436.json",
            rough_summary="summary text",
        )
        text = TelegramReviewBot._sources_text(bot, [record])
        self.assertIn("chatgpt_12345678", text)
        self.assertIn("file: 2026-04-12_185436.json", text)
        self.assertIn("time: 2026-04-12", text)
        self.assertIn("review: /review chatgpt_12345678", text)


if __name__ == "__main__":
    unittest.main()
