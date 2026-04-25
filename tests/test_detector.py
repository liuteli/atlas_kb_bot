import json
import tempfile
import unittest
from pathlib import Path

from app.ingest.chatgpt_detector import ChatGPTSourceDetector, short_source_id


class DetectorTests(unittest.TestCase):
    def test_detect_json_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "chatgpt-export"
            root.mkdir()
            source = root / "sample.json"
            source.write_text(json.dumps({"export_meta": {"page_title": "Sample"}, "messages": []}), encoding="utf-8")
            detector = ChatGPTSourceDetector(root, Path(td) / "state.json")
            records = detector.scan_and_update()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].source_type, "json")

    def test_archive_hides_source_and_records_event(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "chatgpt-export"
            root.mkdir()
            source = root / "sample.json"
            source.write_text(json.dumps({"export_meta": {"page_title": "Sample"}, "messages": []}), encoding="utf-8")
            state_path = Path(td) / "state.json"
            detector = ChatGPTSourceDetector(root, state_path)
            records = detector.scan_and_update()
            archived = detector.archive_sources(
                [short_source_id(records[0].source_id)],
                reason="manual_completed_by_user",
                operator="codex",
            )
            self.assertEqual(len(archived), 1)
            self.assertEqual(detector.scan_and_update(), [])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            source_state = state["sources"][records[0].source_id]
            self.assertEqual(source_state["state"], "archived")
            self.assertEqual(source_state["archived_reason"], "manual_completed_by_user")
            self.assertEqual(source_state["operator"], "codex")
            self.assertEqual(len(state["archive_events"]), 1)


if __name__ == "__main__":
    unittest.main()
