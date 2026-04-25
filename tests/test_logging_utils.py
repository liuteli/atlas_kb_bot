import json
import tempfile
import unittest
from pathlib import Path

from app.common.logging_utils import log_event, setup_logger


class LoggingUtilsTests(unittest.TestCase):
    def test_daily_json_log_redacts_and_bounds_summary(self):
        with tempfile.TemporaryDirectory() as td:
            logger = setup_logger("test_kb_logger", Path(td))
            log_event(
                logger,
                event="command_received",
                command="/whoami",
                user_id="1",
                chat_id="2",
                username="vincent",
                status="received",
                summary="token=12345:abcdefghijklmnopqrstuvwxyz prompt=" + "x" * 900,
            )
            files = list(Path(td).glob("*.log"))
            self.assertEqual(len(files), 1)
            row = json.loads(files[0].read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(row["event"], "command_received")
            self.assertEqual(row["command"], "/whoami")
            self.assertIn("token=<redacted>", row["summary"])
            self.assertIn("prompt=<redacted>", row["summary"])
            self.assertNotIn("abcdefghijklmnopqrstuvwxyz", row["summary"])
            self.assertLessEqual(len(row["summary"]), 500)


if __name__ == "__main__":
    unittest.main()
