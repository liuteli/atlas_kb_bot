import unittest
from datetime import datetime
from types import SimpleNamespace

from app.common.logging_utils import SG_TZ
from app.bot.telegram_bot import TelegramReviewBot


class TelegramBotTests(unittest.TestCase):
    def test_registered_commands_include_whoami(self):
        self.assertIn("/whoami", TelegramReviewBot.registered_commands())
        self.assertIn("/backup_report", TelegramReviewBot.registered_commands())

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

    def test_parse_hhmm(self):
        self.assertEqual(TelegramReviewBot._parse_hhmm("08:05"), (8, 5))

    def test_today_at_uses_singapore_timezone(self):
        now = datetime(2026, 4, 29, 7, 0, tzinfo=SG_TZ)
        scheduled = TelegramReviewBot._today_at("08:05", now)
        self.assertEqual(scheduled.hour, 8)
        self.assertEqual(scheduled.minute, 5)
        self.assertEqual(scheduled.tzinfo, SG_TZ)

    def test_split_message_chunks_long_text(self):
        text = ("alpha\n\n" * 1000).strip()
        chunks = TelegramReviewBot._split_message(text, limit=200)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 200 for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
