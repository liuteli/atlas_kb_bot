import tempfile
import unittest
from pathlib import Path

from app.common.config import load_settings
from app.reports.daily_backup_report import DailyBackupReport, _SshInspectionResult


class DailyBackupReportTests(unittest.TestCase):
    def _build_report(
        self,
        verify_text: str,
        publisher_text: str,
        inspection_result: _SshInspectionResult,
    ) -> str:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            backup_logs = root / "backups" / "logs"
            backup_logs.mkdir(parents=True)
            (backup_logs / "nightly_backup_verify_latest.log").write_text(verify_text, encoding="utf-8")
            (backup_logs / "atlas_icloud_publisher.log").write_text(publisher_text, encoding="utf-8")

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

            report_builder = DailyBackupReport(load_settings(env))
            report_builder._inspect_nas_tgz_via_ssh = lambda _path: inspection_result  # type: ignore[method-assign]
            return report_builder.render()

    def test_render_uses_run_ts_date_and_keeps_obsidian_ok_when_ssh_is_unavailable(self):
        report = self._build_report(
            verify_text=(
                "[INFO] selected MASTER_LOG=/tmp/master.log\n"
                "[INFO] selected RUN_TS=20260503_033001\n"
                "[INFO] selected LOCAL_RUN=/tmp/local-run\n"
                "[INFO] selected NAS_RUN=/tmp/nas-run\n"
                "[INFO] selected NAS_OBSIDIAN_TGZ=/volume1/backups/knowledge/obsidian-atlas/daily/atlas_20260503_033255.tar.gz\n"
                "[INFO] selected publisher success timestamp=2026-05-03 04:05:01\n"
                "[OK] master log final status OK\n"
                "[OK] local log final status OK\n"
                "[OK] remote sync log final status OK\n"
                "[NAS] [OK] NAS Obsidian tgz contains real curated vault content\n"
                "[OK] NAS Obsidian tgz contains real curated vault content\n"
                "[OK] pg_restore list OK: /tmp/atlas.dump\n"
                "[OK] publisher log present: /tmp/publisher.log\n"
                "[OK] infra tools backup verified\n"
                "WARN_COUNT=0\n"
                "ERROR_COUNT=0\n"
                "FINAL_STATUS=OK\n"
            ),
            publisher_text=(
                "[2026-04-30 04:05:02] atlas-icloud-publisher done\n"
                "[2026-05-03 03:32:43] stage obsidian vault start\n"
                "[2026-05-03 03:32:43] source=/Users/liuteli/Library/Mobile Documents/iCloud~md~obsidian/Documents/atlas\n"
                "[2026-05-03 03:32:43] stage obsidian vault done duration_ms=253 result=ok\n"
                "[2026-05-03 04:05:01] atlas-icloud-publisher publish-db-schema start\n"
                "[2026-05-03 04:05:01] atlas-icloud-publisher publish-db-schema done\n"
            ),
            inspection_result=_SshInspectionResult(
                attempted=False,
                exists_non_empty=False,
                tar_list_ok=False,
                size_bytes=None,
                top_level_dirs=set(),
                detail="SSH unavailable: [Errno 2] No such file or directory: 'ssh'",
                stdout="",
                stderr="",
            ),
        )

        self.assertIn("Atlas Daily Backup & Change Report — 2026-05-03", report)
        self.assertNotIn("Atlas Daily Backup & Change Report — 2026-04-30", report)
        self.assertIn("publish-db-schema done: 2026-05-03 04:05:01", report)
        self.assertNotIn("publish-db-schema done: 2026-04-30 04:05:02", report)
        self.assertIn("Obsidian staging: OK (2026-05-03 03:32:43)", report)
        self.assertIn("Verifier result: OK / real curated vault content confirmed", report)
        self.assertIn("Direct inspect: skipped/unavailable", report)
        self.assertIn("Result: OK", report)
        self.assertIn("6. Action Required\n- None", report)

    def test_parse_verify_log_accepts_remote_curated_vault_success_marker(self):
        with tempfile.TemporaryDirectory() as td:
            verify_path = Path(td) / "nightly_backup_verify_latest.log"
            verify_path.write_text(
                "[INFO] selected RUN_TS=20260503_033001\n"
                "[INFO] selected NAS_OBSIDIAN_TGZ=/volume1/backups/knowledge/obsidian-atlas/daily/atlas_20260503_033255.tar.gz\n"
                "[OK] remote Obsidian tgz contains real curated vault content\n"
                "WARN_COUNT=0\n"
                "ERROR_COUNT=0\n"
                "FINAL_STATUS=OK\n",
                encoding="utf-8",
            )

            summary = DailyBackupReport(load_settings(None))._parse_verify_log(verify_path)
            self.assertEqual(summary.nas_obsidian_tgz_integrity, "OK")

    def test_render_includes_required_sections_when_ssh_inspection_passes(self):
        report = self._build_report(
            verify_text=(
                "[INFO] selected MASTER_LOG=/tmp/master.log\n"
                "[INFO] selected RUN_TS=20260429_033001\n"
                "[INFO] selected LOCAL_RUN=/tmp/local-run\n"
                "[INFO] selected NAS_RUN=/tmp/nas-run\n"
                "[INFO] selected NAS_OBSIDIAN_TGZ=/volume1/backups/knowledge/obsidian-atlas/daily/atlas_20260429_033245.tar.gz\n"
                "[INFO] selected publisher success timestamp=2026-04-29 04:05:01\n"
                "[OK] master log final status OK\n"
                "[OK] local log final status OK\n"
                "[OK] remote sync log final status OK\n"
                "[OK] NAS Obsidian tgz integrity OK: /volume1/backups/knowledge/obsidian-atlas/daily/atlas_20260429_033245.tar.gz\n"
                "[OK] pg_restore list OK: /tmp/atlas.dump\n"
                "[OK] publisher log present: /tmp/publisher.log\n"
                "[OK] infra tools backup verified\n"
                "WARN_COUNT=0\n"
                "ERROR_COUNT=0\n"
                "FINAL_STATUS=OK\n"
            ),
            publisher_text=(
                "[2026-04-29 03:32:43] stage obsidian vault start\n"
                "[2026-04-29 03:32:43] source=/Users/liuteli/Library/Mobile Documents/iCloud~md~obsidian/Documents/atlas\n"
                "[2026-04-29 03:32:43] stage obsidian vault done duration_ms=253 result=ok\n"
                "[2026-04-29 04:05:01] atlas-icloud-publisher publish-db-schema start\n"
                "[2026-04-29 04:05:01] atlas-icloud-publisher publish-db-schema done\n"
            ),
            inspection_result=_SshInspectionResult(
                attempted=True,
                exists_non_empty=True,
                tar_list_ok=True,
                size_bytes=12048,
                top_level_dirs={".obsidian", "00_HOME", "01_BOOK", "02_WIKI", "03_INDEX", "31_SCHEMAS", "40_ATTACHMENTS", "copilot"},
                detail="SSH inspection passed",
                stdout="",
                stderr="",
            ),
        )

        self.assertIn("FINAL_STATUS=OK", report)
        self.assertIn("Obsidian KB Tar Backup", report)
        self.assertIn("NAS tgz:", report)
        self.assertIn("Active vault source: /Users/liuteli/Library/Mobile Documents/iCloud~md~obsidian/Documents/atlas", report)
        self.assertIn("publish-db-schema done: 2026-04-29 04:05:01", report)
        self.assertIn("tar list: OK", report)
        self.assertIn("Required dirs: OK", report)
        self.assertIn("Forbidden working dirs: none", report)
        self.assertIn("Result: OK", report)
        self.assertIn("DB Schema Changes", report)
        self.assertIn("Code Changes", report)
        self.assertIn("Action Required", report)


if __name__ == "__main__":
    unittest.main()
