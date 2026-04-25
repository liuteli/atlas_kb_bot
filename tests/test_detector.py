import json
import tempfile
import unittest
from pathlib import Path

from app.ingest.chatgpt_detector import ChatGPTSourceDetector


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


if __name__ == "__main__":
    unittest.main()
