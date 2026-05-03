from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


REQUIRED_PACK_FILES = (
    "source_meta.json",
    "parsed_messages.jsonl",
    "conversation_inventory.md",
    "extracted_candidate_facts.jsonl",
    "stale_or_superseded_items.md",
    "code_verification_report.md",
    "proposed_obsidian_update_plan.md",
    "knowledge_bot_ingest_decision.md",
    "next_codex_prompt_for_targeted_obsidian_update.md",
)
APPLIED_PAGE_NAMES = (
    "GEONAMES_COMMAND_BRIDGE_REFERENCE.md",
    "GEONAMES_VALIDATION_AND_TROUBLESHOOTING.md",
)
PENDING_MANUAL_REVIEW_NAMES = (
    "GEONAMES_SCRIPT_MAP.md",
    "INFRA_NAS_BACKUP_RUNTIME.md",
)
STAGING_REVIEW_PACK_RELATIVE_ROOT = Path("21_STAGING/chat-history-review")
COMPLETED_REVIEW_PACK_RELATIVE_ROOT = Path("11_SOURCES_CLEAN/chat-history-review/completed")


class ExternalReviewPackArchiveError(RuntimeError):
    """Raised when an external review pack cannot be archived safely."""


@dataclass(frozen=True)
class ExternalReviewPackArchiveResult:
    source_pack: Path
    destination_pack: Path
    moved_session_note: Optional[Path]
    status_path: Path
    source_id: Optional[str]
    raw_source_lifecycle_state: Optional[str]


class ExternalReviewPackArchiver:
    """Close an external Codex review pack without touching raw-source state."""

    def __init__(self, knowledge_local_root: Path, chatgpt_export_root: Path, state_root: Path) -> None:
        self.knowledge_local_root = knowledge_local_root
        self.chatgpt_export_root = chatgpt_export_root
        self.state_root = state_root

    @property
    def staging_root(self) -> Path:
        return self.knowledge_local_root / STAGING_REVIEW_PACK_RELATIVE_ROOT

    @property
    def completed_root(self) -> Path:
        return self.knowledge_local_root / COMPLETED_REVIEW_PACK_RELATIVE_ROOT

    @property
    def manifest_path(self) -> Path:
        return self.state_root / "chatgpt_sources.json"

    def archive_pack(self, pack_dir: Path) -> ExternalReviewPackArchiveResult:
        source_pack = pack_dir.expanduser().resolve(strict=True)
        self._require_directory(source_pack)
        self._require_within(source_pack, self.staging_root, "source pack")
        self._validate_required_pack_files(source_pack)

        destination_pack = (self.completed_root / source_pack.name).resolve(strict=False)
        self._require_within(destination_pack, self.completed_root, "destination pack")
        if destination_pack.exists():
            raise ExternalReviewPackArchiveError(f"destination already exists: {destination_pack}")

        source_meta = self._read_json(source_pack / "source_meta.json")
        raw_source_path = self._raw_source_path(source_meta)
        source_root_copy_path = self._source_root_copy_path(raw_source_path)
        manifest_record = self._find_manifest_record(source_root_copy_path)
        session_note = self._find_matching_session_note(
            raw_source_name=raw_source_path.name if raw_source_path is not None else None,
            source_root_copy_path=source_root_copy_path,
            source_id=str(manifest_record.get("source_id")) if manifest_record else None,
        )

        destination_pack.parent.mkdir(parents=True, exist_ok=True)
        moved_pack = source_pack.rename(destination_pack)
        moved_session_note = self._move_session_note(session_note, moved_pack)
        status_path = self._write_status_file(
            source_pack=source_pack,
            destination_pack=moved_pack,
            raw_source_path=raw_source_path,
            source_root_copy_path=source_root_copy_path,
            manifest_record=manifest_record,
        )
        return ExternalReviewPackArchiveResult(
            source_pack=source_pack,
            destination_pack=moved_pack,
            moved_session_note=moved_session_note,
            status_path=status_path,
            source_id=str(manifest_record.get("source_id")) if manifest_record else None,
            raw_source_lifecycle_state=str(manifest_record.get("state")) if manifest_record else None,
        )

    def _require_directory(self, path: Path) -> None:
        if not path.is_dir():
            raise ExternalReviewPackArchiveError(f"pack directory not found: {path}")

    def _validate_required_pack_files(self, pack_dir: Path) -> None:
        missing = [name for name in REQUIRED_PACK_FILES if not (pack_dir / name).is_file()]
        if missing:
            names = ", ".join(missing)
            raise ExternalReviewPackArchiveError(f"pack is missing required files: {names}")

    def _require_within(self, candidate: Path, root: Path, label: str) -> None:
        root_resolved = root.expanduser().resolve(strict=False)
        candidate_resolved = candidate.expanduser().resolve(strict=False)
        if candidate_resolved != root_resolved and root_resolved not in candidate_resolved.parents:
            raise ExternalReviewPackArchiveError(f"{label} is outside allowed root: {candidate_resolved}")

    def _read_json(self, path: Path) -> Dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _raw_source_path(self, source_meta: Dict[str, object]) -> Optional[Path]:
        raw_file = source_meta.get("raw_file")
        if not raw_file:
            return None
        return Path(str(raw_file)).expanduser()

    def _source_root_copy_path(self, raw_source_path: Optional[Path]) -> Optional[Path]:
        if raw_source_path is None or not raw_source_path.name:
            return None
        return (self.chatgpt_export_root / raw_source_path.name).resolve(strict=False)

    def _find_manifest_record(self, source_root_copy_path: Optional[Path]) -> Optional[Dict[str, object]]:
        if source_root_copy_path is None or not self.manifest_path.exists():
            return None
        state = self._read_json(self.manifest_path)
        sources = state.get("sources") or {}
        for record in sources.values():
            if not isinstance(record, dict):
                continue
            record_path = record.get("path")
            if not record_path:
                continue
            if Path(str(record_path)).expanduser().resolve(strict=False) == source_root_copy_path:
                return record
        return None

    def _find_matching_session_note(
        self,
        *,
        raw_source_name: Optional[str],
        source_root_copy_path: Optional[Path],
        source_id: Optional[str],
    ) -> Optional[Path]:
        if not self.staging_root.exists():
            return None
        needles = [item for item in [
            raw_source_name,
            str(source_root_copy_path) if source_root_copy_path is not None else None,
            source_id,
        ] if item]
        if not needles:
            return None

        matches = []
        for candidate in sorted(self.staging_root.glob("*.md")):
            if not candidate.is_file():
                continue
            text = candidate.read_text(encoding="utf-8", errors="replace")
            if any(needle in text for needle in needles):
                matches.append(candidate)
        if len(matches) > 1:
            names = ", ".join(str(path) for path in matches)
            raise ExternalReviewPackArchiveError(f"multiple matching staging session notes found: {names}")
        return matches[0] if matches else None

    def _move_session_note(self, session_note: Optional[Path], destination_pack: Path) -> Optional[Path]:
        if session_note is None:
            return None
        session_notes_dir = destination_pack / "_session_notes"
        session_notes_dir.mkdir(parents=True, exist_ok=True)
        destination_note = session_notes_dir / session_note.name
        return session_note.rename(destination_note)

    def _write_status_file(
        self,
        *,
        source_pack: Path,
        destination_pack: Path,
        raw_source_path: Optional[Path],
        source_root_copy_path: Optional[Path],
        manifest_record: Optional[Dict[str, object]],
    ) -> Path:
        closure_timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        closure_command = f"python3 -m app.cli archive-external-review-pack {source_pack}"
        status_path = destination_pack / "_STATUS.md"
        source_id = str(manifest_record.get("source_id")) if manifest_record else "unknown"
        raw_state = str(manifest_record.get("state")) if manifest_record else "unknown"

        lines = [
            "# External Codex Review Pack Status",
            "",
            "- Status: completed_safe_apply",
            f"- Pack name: {destination_pack.name}",
            f"- Original staging path: {source_pack}",
            f"- Completed path: {destination_pack}",
            f"- Raw source path: {raw_source_path if raw_source_path is not None else 'unknown'}",
            f"- Source-root copy path: {source_root_copy_path if source_root_copy_path is not None else 'unknown'}",
            f"- Source ID: {source_id}",
            f"- Raw source lifecycle state: {raw_state}",
            "- Applied pages:",
        ]
        for page_name in APPLIED_PAGE_NAMES:
            lines.append(f"  - {page_name}")
        lines.append("- Skipped / pending manual review:")
        for page_name in PENDING_MANUAL_REVIEW_NAMES:
            lines.append(f"  - {page_name}")
        lines.extend([
            f"- Closure timestamp: {closure_timestamp}",
            f"- Closure command: `{closure_command}`",
            "- Note: raw chat was not wholesale-ingested into Obsidian.",
            "- Note: this is an external Codex review pack, not Telegram /review output.",
            "- Note: this lifecycle is separate from raw source archive-source.",
            "",
        ])
        status_path.write_text("\n".join(lines), encoding="utf-8")
        return status_path
