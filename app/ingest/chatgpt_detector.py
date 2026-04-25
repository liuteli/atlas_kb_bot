from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List

from app.common.json_store import JsonStore


GOVERNANCE_DIRS = {"incoming", "processing", "archived", "rejected"}
IGNORED_NAMES = {".DS_Store", "FILE_LIST.txt", "FILE_INDEX.md"}


@dataclass
class SourceRecord:
    """A detected source unit awaiting review."""

    source_id: str
    source_type: str
    path: str
    display_name: str
    rough_summary: str
    size_bytes: int
    state: str = "detected"


class ChatGPTSourceDetector:
    """Detect ChatGPT chat exports without moving or modifying raw files."""

    def __init__(self, source_root: Path, state_path: Path) -> None:
        self.source_root = source_root
        self.store = JsonStore(state_path)

    def scan_and_update(self) -> List[SourceRecord]:
        """Scan source root, add newly detected records to state, return active records."""
        state = self.store.read()
        sources: Dict[str, Dict[str, object]] = state.setdefault("sources", {})
        detected = list(self.scan())
        for record in detected:
            if record.source_id not in sources:
                sources[record.source_id] = asdict(record)
        self.store.write(state)
        return [SourceRecord(**value) for value in sources.values() if value.get("state") in {"detected", "reviewed", "review_failed"}]

    def scan(self) -> Iterable[SourceRecord]:
        """Yield source records from incoming/ if present, otherwise from root."""
        scan_root = self.source_root / "incoming"
        if not scan_root.exists():
            scan_root = self.source_root
        if not scan_root.exists():
            return []
        records: List[SourceRecord] = []
        for item in sorted(scan_root.iterdir(), key=lambda p: p.name):
            if item.name in IGNORED_NAMES or item.name.startswith("."):
                continue
            if item.is_dir() and item.name in GOVERNANCE_DIRS:
                continue
            record = self._record_for(item)
            if record is not None:
                records.append(record)
        return records

    def _record_for(self, path: Path) -> SourceRecord:
        source_type = self._source_type(path)
        if source_type == "unknown":
            return None
        rel = str(path.relative_to(self.source_root)) if path.is_relative_to(self.source_root) else str(path)
        source_id = "chatgpt_" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:12]
        return SourceRecord(
            source_id=source_id,
            source_type=source_type,
            path=str(path),
            display_name=path.name,
            rough_summary=self._rough_summary(path, source_type),
            size_bytes=self._size_bytes(path),
        )

    def _source_type(self, path: Path) -> str:
        if path.is_dir():
            md_parts = list(path.glob("*.md"))
            if (path / "manifest.json.txt").exists() or len(md_parts) > 1:
                return "multipart_md_dir"
            return "unknown"
        suffix = path.suffix.lower()
        if suffix == ".json":
            return "json"
        if suffix == ".md":
            return "md"
        if path.name.endswith(".json.txt"):
            return "json_text"
        return "unknown"

    def _rough_summary(self, path: Path, source_type: str) -> str:
        try:
            if source_type == "json":
                obj = json.loads(path.read_text(encoding="utf-8"))
                meta = obj.get("export_meta") or {}
                title = meta.get("page_title") or path.name
                count = meta.get("message_count") or len(obj.get("messages") or [])
                return f"{title}; messages={count}"[:160]
            if source_type == "multipart_md_dir":
                parts = sorted(path.glob("*.md"))
                return f"multipart markdown chat export; parts={len(parts)}"[:160]
            text = path.read_text(encoding="utf-8", errors="replace")
            first = next((line.strip("# ").strip() for line in text.splitlines() if line.strip()), path.name)
            return first[:160]
        except Exception as exc:
            return f"summary_error:{exc}"[:160]

    def _size_bytes(self, path: Path) -> int:
        if path.is_file():
            return path.stat().st_size
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
