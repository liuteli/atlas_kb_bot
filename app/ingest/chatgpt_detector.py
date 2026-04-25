from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from app.common.json_store import JsonStore


GOVERNANCE_DIRS = {"incoming", "processing", "archived", "rejected"}
IGNORED_NAMES = {".DS_Store", "FILE_LIST.txt", "FILE_INDEX.md"}
ACTIVE_STATES = {"detected", "reviewed", "review_failed"}
TERMINAL_STATES = {"archived", "completed", "rejected"}


def short_source_id(source_id: str) -> str:
    """Return a readable short alias such as chatgpt_12345678."""
    if source_id.startswith("chatgpt_") and len(source_id) > 16:
        return source_id[:16]
    return source_id


@dataclass
class SourceRecord:
    """A detected source unit tracked by the source-state manifest."""

    source_id: str
    source_type: str
    path: str
    display_name: str
    rough_summary: str
    size_bytes: int
    state: str = "detected"
    archived_at: Optional[str] = None
    archived_reason: Optional[str] = None
    operator: Optional[str] = None


class ChatGPTSourceDetector:
    """Detect and track ChatGPT chat exports without moving raw files."""

    def __init__(self, source_root: Path, state_path: Path) -> None:
        self.source_root = source_root
        self.store = JsonStore(state_path)

    def scan_and_update(self) -> List[SourceRecord]:
        """Scan source root, add new records, and return active pending records."""
        state = self.store.read()
        sources: Dict[str, Dict[str, object]] = state.setdefault("sources", {})
        detected = list(self.scan())
        for record in detected:
            existing = sources.get(record.source_id)
            if existing is None:
                sources[record.source_id] = asdict(record)
            elif existing.get("state") not in TERMINAL_STATES:
                # Keep path/display metadata fresh without reviving archived sources.
                previous_state = existing.get("state", "detected")
                merged = asdict(record)
                merged["state"] = previous_state
                sources[record.source_id].update(merged)
        self.store.write(state)
        return self.active_records(state)

    def active_records(self, state: Optional[Dict[str, object]] = None) -> List[SourceRecord]:
        """Return sources that should appear in /sources and review queues."""
        state = state if state is not None else self.store.read()
        sources = state.get("sources") or {}
        records = []
        for value in sources.values():
            if value.get("state") in ACTIVE_STATES:
                records.append(SourceRecord(**value))
        return records

    def archive_sources(
        self,
        source_ids: List[str],
        *,
        reason: str,
        operator: str,
    ) -> List[Dict[str, object]]:
        """Mark sources as archived/completed without deleting raw source files."""
        state = self.store.read()
        sources: Dict[str, Dict[str, object]] = state.setdefault("sources", {})
        # Ensure the manifest contains currently visible files before archiving.
        for record in self.scan():
            sources.setdefault(record.source_id, asdict(record))

        archived_at = datetime.now(timezone.utc).isoformat()
        events = state.setdefault("archive_events", [])
        archived: List[Dict[str, object]] = []
        for requested_id in source_ids:
            source_id = self.resolve_source_id(requested_id, sources)
            record = sources[source_id]
            previous_state = str(record.get("state", "detected"))
            record["state"] = "archived"
            record["archived_at"] = archived_at
            record["archived_reason"] = reason
            record["operator"] = operator
            event = {
                "source_id": source_id,
                "short_source_id": short_source_id(source_id),
                "original_path": record.get("path"),
                "display_name": record.get("display_name"),
                "previous_state": previous_state,
                "state": "archived",
                "archived_at": archived_at,
                "archived_reason": reason,
                "operator": operator,
            }
            events.append(event)
            archived.append(event)
        self.store.write(state)
        return archived

    def resolve_source_id(self, source_id_or_prefix: str, sources: Optional[Dict[str, Dict[str, object]]] = None) -> str:
        """Resolve an exact source id or unique short prefix to the full id."""
        sources = sources if sources is not None else (self.store.read().get("sources") or {})
        if source_id_or_prefix in sources:
            return source_id_or_prefix
        matches = [source_id for source_id in sources if source_id.startswith(source_id_or_prefix)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise KeyError(f"ambiguous source id prefix: {source_id_or_prefix}")
        raise KeyError(f"source not found: {source_id_or_prefix}")

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

    def _record_for(self, path: Path) -> Optional[SourceRecord]:
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
