import tempfile
import unittest
from pathlib import Path

from app.common.config import CANONICAL_ENV_CONTRACT, load_settings


class ConfigTests(unittest.TestCase):
    def test_load_settings_from_env_file(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td) / ".env"
            env.write_text(
                "KB_STATE_ROOT=/tmp/kb-state-test\n"
                "KB_TELEGRAM_BOT_TOKEN=abc\n"
                "GITHUB_REPO_URL=https://github.com/liuteli/atlas.git\n",
                encoding="utf-8",
            )
            settings = load_settings(env)
            self.assertEqual(str(settings.state_root), "/tmp/kb-state-test")
            self.assertEqual(settings.telegram_bot_token, "abc")
            self.assertEqual(settings.github_repo_url, "https://github.com/liuteli/atlas.git")

    def test_canonical_contract_count(self):
        self.assertEqual(len(CANONICAL_ENV_CONTRACT), 21)
        self.assertIn("GITHUB_BOT_REPO_URL", CANONICAL_ENV_CONTRACT)
        self.assertIn("KB_ALLOWED_CHAT_IDS", CANONICAL_ENV_CONTRACT)


if __name__ == "__main__":
    unittest.main()
