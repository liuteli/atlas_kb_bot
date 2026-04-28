from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from app.common.config import Settings
from app.common.subprocess_utils import run_cmd


MAX_SECTION_LINES = 6
VERIFY_VALUE_RE = re.compile(r"^\[INFO\] selected ([A-Za-z_ ]+?)=(.+)$")
SUMMARY_VALUE_RE = re.compile(r"^([A-Z_]+)=(.+)$")
PUBLISHER_LINE_RE = re.compile(r"^\[(?P<ts>[^\]]+)\] (?P<body>.+)$")
DIFF_PATH_RE = re.compile(
    r"^(?:---|\+\+\+) .+/(local|cloud)/schemas/([^/]+)/(tables|views|matviews|functions)/([^/\s]+)\.md"
)


@dataclass(frozen=True)
class VerifySummary:
    final_status: str = "UNKNOWN"
    warn_count: str = "?"
    error_count: str = "?"
    run_ts: str = "unknown"
    master_log: str = "unknown"
    local_run: str = "unknown"
    local_log: str = "unknown"
    remote_log: str = "unknown"
    nas_run: str = "unknown"
    publisher_success_ts: str = "unknown"
    main_backup_status: str = "unknown"
    local_run_status: str = "unknown"
    nas_run_status: str = "unknown"
    postgres_dump_sanity: str = "unknown"
    tools_backup_sanity: str = "unknown"
    icloud_publisher_sanity: str = "unknown"


@dataclass(frozen=True)
class SchemaDiffSummary:
    path: str
    local_tables: List[str]
    cloud_tables: List[str]
    other_changes: List[str]


@dataclass(frozen=True)
class CodeDiffSummary:
    path: str
    head_changed: Optional[bool]
    artifact_summary: List[str]
    changed_files: List[str]


class DailyBackupReport:
    """Render a concise daily backup and change report for Telegram."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def render(self) -> str:
        verify = self._parse_verify_log(self.settings.backup_log_root / "nightly_backup_verify_latest.log")
        publisher_done_ts = self._parse_publisher_done_timestamp(self.settings.backup_log_root / "atlas_icloud_publisher.log")
        schema = self._parse_schema_diff(self._latest_artifact_path(self.settings.db_schema_diff_root))
        code = self._parse_code_diff(self._latest_artifact_path(self.settings.github_diff_root))
        backup_script_commits = self._git_log_since(self.settings.backup_scripts_root)
        atlas_commits = self._git_log_since(self.settings.atlas_repo_path)
        title_date = (publisher_done_ts or verify.publisher_success_ts or verify.run_ts)[:10].replace("_", "-")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", title_date):
            title_date = "unknown-date"

        overall_lines = [
            f"- status: {self._status_marker(verify.final_status, verify.warn_count, verify.error_count)} FINAL_STATUS={verify.final_status}",
            f"- WARN_COUNT={verify.warn_count} ERROR_COUNT={verify.error_count}",
            f"- RUN_TS={verify.run_ts}",
            f"- publisher_success_ts={verify.publisher_success_ts}",
        ]
        if publisher_done_ts and publisher_done_ts != verify.publisher_success_ts:
            overall_lines.append(f"- publisher_done_ts={publisher_done_ts}")

        backup_lines = [
            f"- main backup: {verify.main_backup_status}",
            f"- local run: {verify.local_run_status} ({verify.local_run})",
            f"- NAS run: {verify.nas_run_status} ({verify.nas_run})",
            f"- Postgres dump sanity: {verify.postgres_dump_sanity}",
            f"- tools backup sanity: {verify.tools_backup_sanity}",
            f"- iCloud publisher sanity: {verify.icloud_publisher_sanity}",
            f"- verifier log: {self.settings.backup_log_root / 'nightly_backup_verify_latest.log'}",
        ]

        schema_lines = self._schema_section_lines(schema)
        code_lines = self._code_section_lines(code, backup_script_commits, atlas_commits)
        action_lines = self._action_required_lines(verify)
        reference_lines = [
            f"- {self.settings.backup_log_root / 'nightly_backup_verify_latest.log'}",
            f"- {verify.master_log}",
            f"- {self.settings.backup_log_root / 'atlas_icloud_publisher.log'}",
            f"- {schema.path}",
            f"- {code.path}",
        ]

        sections = [
            f"Atlas Daily Backup & Change Report — {title_date}",
            "",
            "1. Overall Status",
            *overall_lines,
            "",
            "2. Backup Verification",
            *backup_lines,
            "",
            "3. DB Schema Changes",
            *schema_lines,
            "",
            "4. Code Changes",
            *code_lines,
            "",
            "5. Action Required",
            *action_lines,
            "",
            "6. Reference Logs",
            *reference_lines,
        ]
        return "\n".join(sections)

    def _latest_artifact_path(self, root: Path) -> Path:
        if (root / "latest.md").exists():
            return root / "latest.md"
        latest = root / "atlas" / "latest.md"
        if latest.exists():
            return latest
        return root / "latest.md"

    def _parse_verify_log(self, path: Path) -> VerifySummary:
        if not path.exists():
            return VerifySummary()
        info: Dict[str, str] = {}
        summary: Dict[str, str] = {}
        statuses: Dict[str, str] = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            info_match = VERIFY_VALUE_RE.match(line.strip())
            if info_match:
                key = info_match.group(1).strip().replace(" ", "_").lower()
                info[key] = info_match.group(2).strip()
            summary_match = SUMMARY_VALUE_RE.match(line.strip())
            if summary_match:
                summary[summary_match.group(1).strip()] = summary_match.group(2).strip()
            if "master log final status OK" in line:
                statuses["main_backup_status"] = "OK"
            if "local log final status OK" in line:
                statuses["local_run_status"] = "OK"
            if "remote sync log final status OK" in line:
                statuses["nas_run_status"] = "OK"
            if "pg_restore list OK" in line:
                statuses["postgres_dump_sanity"] = "OK"
            if "infra tools backup verified" in line:
                statuses["tools_backup_sanity"] = "OK"
            if "publisher log present" in line or "publisher schema residue cleanup logged" in line:
                statuses["icloud_publisher_sanity"] = "OK"
        return VerifySummary(
            final_status=summary.get("FINAL_STATUS", "UNKNOWN"),
            warn_count=summary.get("WARN_COUNT", "?"),
            error_count=summary.get("ERROR_COUNT", "?"),
            run_ts=info.get("run_ts", "unknown"),
            master_log=info.get("master_log", "unknown"),
            local_run=info.get("local_run", "unknown"),
            local_log=info.get("local_log", "unknown"),
            remote_log=info.get("remote_log", "unknown"),
            nas_run=info.get("nas_run", "unknown"),
            publisher_success_ts=info.get("publisher_success_timestamp", "unknown"),
            main_backup_status=statuses.get("main_backup_status", "unknown"),
            local_run_status=statuses.get("local_run_status", "unknown"),
            nas_run_status=statuses.get("nas_run_status", "unknown"),
            postgres_dump_sanity=statuses.get("postgres_dump_sanity", "unknown"),
            tools_backup_sanity=statuses.get("tools_backup_sanity", "unknown"),
            icloud_publisher_sanity=statuses.get("icloud_publisher_sanity", "unknown"),
        )

    def _parse_publisher_done_timestamp(self, path: Path) -> Optional[str]:
        if not path.exists():
            return None
        for line in reversed(path.read_text(encoding="utf-8", errors="replace").splitlines()):
            match = PUBLISHER_LINE_RE.match(line.strip())
            if not match:
                continue
            if "atlas-icloud-publisher done" in match.group("body"):
                return match.group("ts")
        return None

    def _parse_schema_diff(self, path: Path) -> SchemaDiffSummary:
        if not path.exists():
            return SchemaDiffSummary(path=str(path), local_tables=[], cloud_tables=[], other_changes=[])
        text = path.read_text(encoding="utf-8", errors="replace")
        local_tables: set[str] = set()
        cloud_tables: set[str] = set()
        other_changes: set[str] = set()
        for line in text.splitlines():
            match = DIFF_PATH_RE.match(line.strip())
            if not match:
                continue
            side, schema_name, object_kind, object_name = match.groups()
            item = f"{schema_name}.{object_name} ({object_kind})"
            if object_kind == "tables":
                if side == "local":
                    local_tables.add(item)
                else:
                    cloud_tables.add(item)
            else:
                other_changes.add(f"{side} {item}")
        return SchemaDiffSummary(
            path=str(path),
            local_tables=sorted(local_tables),
            cloud_tables=sorted(cloud_tables),
            other_changes=sorted(other_changes),
        )

    def _parse_code_diff(self, path: Path) -> CodeDiffSummary:
        if not path.exists():
            return CodeDiffSummary(path=str(path), head_changed=None, artifact_summary=[], changed_files=[])
        summary: List[str] = []
        changed_files: List[str] = []
        head_changed: Optional[bool] = None
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if line == "HEAD changed.":
                head_changed = True
            elif line == "HEAD unchanged.":
                head_changed = False
            elif line.startswith("- previous head:") or line.startswith("- current head:"):
                summary.append(line[2:])
            elif line.startswith("- changed: `"):
                changed_files.append(line[2:].replace("`", ""))
            elif line.startswith("- unchanged: `") and len(summary) < MAX_SECTION_LINES:
                summary.append(line[2:].replace("`", ""))
            elif line.startswith("+") and not line.startswith("+++"):
                commit = line[1:].strip()
                if re.match(r"^[0-9a-f]{7,}\s", commit):
                    summary.append(f"recent diff commit: {commit}")
        return CodeDiffSummary(
            path=str(path),
            head_changed=head_changed,
            artifact_summary=self._limit_lines(summary),
            changed_files=self._limit_lines(changed_files),
        )

    def _git_log_since(self, repo_path: Path) -> List[str]:
        result = run_cmd(["git", "-C", str(repo_path), "log", "--oneline", "--since=24 hours ago"])
        if result.returncode != 0:
            return [f"git log failed for {repo_path}: {result.stderr.strip() or 'unknown error'}"]
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return lines[:MAX_SECTION_LINES]

    def _schema_section_lines(self, summary: SchemaDiffSummary) -> List[str]:
        lines: List[str] = []
        if summary.local_tables:
            lines.append(f"- local changed tables: {', '.join(summary.local_tables[:4])}")
        if summary.cloud_tables:
            lines.append(f"- cloud changed tables: {', '.join(summary.cloud_tables[:4])}")
        if not summary.local_tables and not summary.cloud_tables:
            lines.append("- No table-level DB schema changes detected from latest artifacts.")
        if summary.other_changes:
            lines.append(f"- other schema artifact changes: {', '.join(summary.other_changes[:4])}")
        lines.append(f"- artifact: {summary.path}")
        return lines

    def _code_section_lines(
        self,
        summary: CodeDiffSummary,
        backup_script_commits: List[str],
        atlas_commits: List[str],
    ) -> List[str]:
        lines: List[str] = []
        lines.append("- backup scripts commits last 24h:")
        lines.extend(self._prefixed_or_none(backup_script_commits))
        lines.append("- Atlas repo commits last 24h:")
        lines.extend(self._prefixed_or_none(atlas_commits))
        if summary.head_changed is not None:
            lines.append(f"- github diff artifact HEAD changed: {'yes' if summary.head_changed else 'no'}")
        if summary.changed_files:
            lines.append(f"- github diff artifact changed files: {', '.join(summary.changed_files[:4])}")
        elif not backup_script_commits and not atlas_commits and summary.head_changed is False:
            lines.append("- No code changes detected from latest artifacts.")
        if summary.artifact_summary:
            lines.append(f"- github diff artifact summary: {'; '.join(summary.artifact_summary[:3])}")
        lines.append(f"- artifact: {summary.path}")
        return lines

    def _action_required_lines(self, verify: VerifySummary) -> List[str]:
        if verify.final_status == "OK" and verify.warn_count == "0" and verify.error_count == "0":
            return ["- None"]
        actions = [f"- Review verifier log: {self.settings.backup_log_root / 'nightly_backup_verify_latest.log'}"]
        if verify.final_status != "OK":
            actions.append(f"- Investigate FINAL_STATUS={verify.final_status}.")
        if verify.warn_count != "0":
            actions.append(f"- Clear WARN_COUNT={verify.warn_count}.")
        if verify.error_count != "0":
            actions.append(f"- Clear ERROR_COUNT={verify.error_count}.")
        return actions

    def _status_marker(self, final_status: str, warn_count: str, error_count: str) -> str:
        if final_status == "OK" and warn_count == "0" and error_count == "0":
            return "✅"
        if error_count not in {"0", "?"} or final_status not in {"OK", "UNKNOWN"}:
            return "❌"
        return "⚠️"

    def _prefixed_or_none(self, lines: Iterable[str]) -> List[str]:
        materialized = [line for line in lines if line]
        if not materialized:
            return ["  none"]
        return [f"  {line}" for line in materialized[:MAX_SECTION_LINES]]

    def _limit_lines(self, lines: List[str]) -> List[str]:
        seen = []
        for line in lines:
            if line not in seen:
                seen.append(line)
        return seen[:MAX_SECTION_LINES]
