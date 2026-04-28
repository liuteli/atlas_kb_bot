import tempfile
import unittest
from pathlib import Path

from app.common.config import load_settings
from app.reports.daily_backup_report import DailyBackupReport


class DailyBackupReportTests(unittest.TestCase):
    def test_render_includes_required_sections(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            backup_logs = root / "backups" / "logs"
            backup_logs.mkdir(parents=True)
            verify = backup_logs / "nightly_backup_verify_latest.log"
            verify.write_text(
                "[INFO] selected MASTER_LOG=/tmp/master.log\n"
                "[INFO] selected RUN_TS=20260429_033001\n"
                "[INFO] selected LOCAL_RUN=/tmp/local-run\n"
                "[INFO] selected NAS_RUN=/tmp/nas-run\n"
                "[INFO] selected publisher success timestamp=2026-04-29 04:05:01\n"
                "[OK] master log final status OK\n"
                "[OK] local log final status OK\n"
                "[OK] remote sync log final status OK\n"
                "[OK] pg_restore list OK: /tmp/atlas.dump\n"
                "[OK] publisher log present: /tmp/publisher.log\n"
                "[OK] infra tools backup verified\n"
                "WARN_COUNT=0\n"
                "ERROR_COUNT=0\n"
                "FINAL_STATUS=OK\n",
                encoding="utf-8",
            )
            (backup_logs / "atlas_icloud_publisher.log").write_text(
                "[2026-04-29 04:05:01] atlas-icloud-publisher done\n",
                encoding="utf-8",
            )
            db_diff_root = root / "db-schema-diffs"
            db_diff_root.mkdir(parents=True)
            (db_diff_root / "latest.md").write_text(
                "+++ /tmp/cloud/schemas/public/tables/users.md\n",
                encoding="utf-8",
            )
            github_diff_root = root / "github-diffs" / "atlas"
            github_diff_root.mkdir(parents=True)
            (github_diff_root / "latest.md").write_text(
                "HEAD changed.\n"
                "- current head: `abcdef123456`\n"
                "- changed: `git_log_recent.txt`\n"
                "+abcdef1 Update report pipeline\n",
                encoding="utf-8",
            )
            backup_scripts_root = root / "backup-scripts"
            backup_scripts_root.mkdir(parents=True)
            atlas_repo_root = root / "atlas"
            atlas_repo_root.mkdir(parents=True)
            env = root / ".env"
            env.write_text(
                f"KB_BACKUP_LOG_ROOT={backup_logs}\n"
                f"KB_BACKUP_SCRIPTS_ROOT={backup_scripts_root}\n"
                f"KB_DB_SCHEMA_DIFF_ROOT={db_diff_root}\n"
                f"KB_GITHUB_DIFF_ROOT={github_diff_root}\n"
                f"KB_ATLAS_REPO_PATH={atlas_repo_root}\n",
                encoding="utf-8",
            )
            report = DailyBackupReport(load_settings(env)).render()
            self.assertIn("FINAL_STATUS=OK", report)
            self.assertIn("DB Schema Changes", report)
            self.assertIn("Code Changes", report)
            self.assertIn("Action Required", report)


if __name__ == "__main__":
    unittest.main()
