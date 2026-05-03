import json
import tempfile
import unittest
from pathlib import Path

from app.ingest.external_review_pack_archiver import ExternalReviewPackArchiveError, ExternalReviewPackArchiver


class ExternalReviewPackArchiverTests(unittest.TestCase):
    def _write_required_pack(self, pack_dir: Path) -> None:
        pack_dir.mkdir(parents=True)
        files = {
            "source_meta.json": json.dumps({
                "raw_file": "/tmp/chat_history/2026-05-03_165934.json",
                "message_count": 10,
            }, ensure_ascii=False, indent=2),
            "parsed_messages.jsonl": "{}\n",
            "conversation_inventory.md": "# inventory\n",
            "extracted_candidate_facts.jsonl": "{}\n",
            "stale_or_superseded_items.md": "# stale\n",
            "code_verification_report.md": "# verification\n",
            "proposed_obsidian_update_plan.md": "# plan\n",
            "knowledge_bot_ingest_decision.md": "# decision\n",
            "next_codex_prompt_for_targeted_obsidian_update.md": "# next\n",
        }
        for name, body in files.items():
            (pack_dir / name).write_text(body, encoding="utf-8")

    def _write_manifest(self, state_root: Path, source_root_copy_path: Path) -> Path:
        state_root.mkdir(parents=True)
        state_path = state_root / "chatgpt_sources.json"
        state_path.write_text(json.dumps({
            "sources": {
                "chatgpt_2899d3678a28": {
                    "source_id": "chatgpt_2899d3678a28",
                    "path": str(source_root_copy_path),
                    "state": "archived",
                    "display_name": source_root_copy_path.name,
                }
            },
            "archive_events": [],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return state_path

    def test_archive_pack_moves_pack_note_and_writes_status_without_mutating_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "atlas"
            staging_root = root / "21_STAGING" / "chat-history-review"
            completed_root = root / "11_SOURCES_CLEAN" / "chat-history-review" / "completed"
            pack_dir = staging_root / "2026-05-03_165934__20260503_175844"
            self._write_required_pack(pack_dir)

            source_export_root = root / "10_SOURCES_RAW" / "chatgpt-export"
            source_export_root.mkdir(parents=True)
            source_root_copy = source_export_root / "2026-05-03_165934.json"
            source_root_copy.write_text("{}", encoding="utf-8")

            session_note = staging_root / "20260503_215552__geonames_safe_plan_apply.md"
            session_note.write_text(
                "# Session Note\n\n"
                "- Source: staged review pack for chat export `2026-05-03_165934.json`.\n",
                encoding="utf-8",
            )

            state_root = root / "99_SYSTEM" / "jobs" / "knowledge-bot"
            state_path = self._write_manifest(state_root, source_root_copy)
            manifest_before = state_path.read_text(encoding="utf-8")

            archiver = ExternalReviewPackArchiver(root, source_export_root, state_root)
            result = archiver.archive_pack(pack_dir)

            self.assertFalse(pack_dir.exists())
            self.assertTrue(completed_root.exists())
            self.assertEqual(result.destination_pack, (completed_root / pack_dir.name).resolve())
            self.assertTrue((result.destination_pack / "source_meta.json").is_file())
            self.assertEqual(
                result.moved_session_note,
                result.destination_pack / "_session_notes" / session_note.name,
            )
            self.assertTrue(result.moved_session_note.is_file())
            self.assertTrue(result.status_path.is_file())
            status_text = result.status_path.read_text(encoding="utf-8")
            self.assertIn("Status: completed_safe_apply", status_text)
            self.assertIn(f"Pack name: {pack_dir.name}", status_text)
            self.assertIn(f"Original staging path: {pack_dir.resolve()}", status_text)
            self.assertIn(f"Completed path: {result.destination_pack}", status_text)
            self.assertIn("Raw source path: /tmp/chat_history/2026-05-03_165934.json", status_text)
            self.assertIn(f"Source-root copy path: {source_root_copy.resolve()}", status_text)
            self.assertIn("Source ID: chatgpt_2899d3678a28", status_text)
            self.assertIn("Raw source lifecycle state: archived", status_text)
            self.assertIn("GEONAMES_COMMAND_BRIDGE_REFERENCE.md", status_text)
            self.assertIn("INFRA_NAS_BACKUP_RUNTIME.md", status_text)
            self.assertIn("external Codex review pack, not Telegram /review output", status_text)
            self.assertIn("separate from raw source archive-source", status_text)
            self.assertEqual(state_path.read_text(encoding="utf-8"), manifest_before)

    def test_refuses_pack_outside_allowed_staging_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "atlas"
            source_export_root = root / "10_SOURCES_RAW" / "chatgpt-export"
            state_root = root / "99_SYSTEM" / "jobs" / "knowledge-bot"
            outside_pack = Path(td) / "outside-pack"
            self._write_required_pack(outside_pack)

            archiver = ExternalReviewPackArchiver(root, source_export_root, state_root)
            with self.assertRaises(ExternalReviewPackArchiveError):
                archiver.archive_pack(outside_pack)

    def test_refuses_missing_required_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "atlas"
            staging_root = root / "21_STAGING" / "chat-history-review"
            pack_dir = staging_root / "2026-05-03_165934__20260503_175844"
            self._write_required_pack(pack_dir)
            (pack_dir / "knowledge_bot_ingest_decision.md").unlink()

            archiver = ExternalReviewPackArchiver(
                root,
                root / "10_SOURCES_RAW" / "chatgpt-export",
                root / "99_SYSTEM" / "jobs" / "knowledge-bot",
            )
            with self.assertRaises(ExternalReviewPackArchiveError):
                archiver.archive_pack(pack_dir)

    def test_refuses_existing_destination(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "atlas"
            staging_root = root / "21_STAGING" / "chat-history-review"
            completed_root = root / "11_SOURCES_CLEAN" / "chat-history-review" / "completed"
            pack_dir = staging_root / "2026-05-03_165934__20260503_175844"
            self._write_required_pack(pack_dir)
            (completed_root / pack_dir.name).mkdir(parents=True)

            archiver = ExternalReviewPackArchiver(
                root,
                root / "10_SOURCES_RAW" / "chatgpt-export",
                root / "99_SYSTEM" / "jobs" / "knowledge-bot",
            )
            with self.assertRaises(ExternalReviewPackArchiveError):
                archiver.archive_pack(pack_dir)


if __name__ == "__main__":
    unittest.main()
