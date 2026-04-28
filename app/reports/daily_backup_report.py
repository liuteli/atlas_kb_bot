from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.common.config import Settings
from app.common.subprocess_utils import run_cmd


MAX_SECTION_LINES = 6
VERIFY_VALUE_RE = re.compile(r"^\[INFO\] selected ([A-Za-z_ ]+?)=(.+)$")
SUMMARY_VALUE_RE = re.compile(r"^([A-Z_]+)=(.+)$")
PUBLISHER_LINE_RE = re.compile(r"^\[(?P<ts>[^\]]+)\] (?P<body>.+)$")
DIFF_PATH_RE = re.compile(
    r"^(?:---|\+\+\+) .+/(local|cloud)/schemas/([^/]+)/(tables|views|matviews|functions)/([^/\s]+)\.md"
)
SIZE_RE = re.compile(r"^(?P<size>\d+)\s+(?P<path>/volume1/.+)$")
TGZ_PREFIXES = ("", "atlas/")
RETAINED_DIRS = (".obsidian", "00_HOME", "01_BOOK", "02_WIKI", "03_INDEX", "31_SCHEMAS", "40_ATTACHMENTS", "copilot")
REQUIRED_DIRS = ("00_HOME", "02_WIKI", "03_INDEX")
FORBIDDEN_DIRS = ("10_SOURCES_RAW", "11_SOURCES_CLEAN", "12_MIRRORS", "20_INBOX", "21_STAGING", "30_TEMPLATES", "99_SYSTEM")
NAS_SSH_TARGET = "liuteli@192.168.50.10"


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
    nas_obsidian_tgz: str = "unknown"
    publisher_success_ts: str = "unknown"
    main_backup_status: str = "unknown"
    local_run_status: str = "unknown"
    nas_run_status: str = "unknown"
    postgres_dump_sanity: str = "unknown"
    tools_backup_sanity: str = "unknown"
    icloud_publisher_sanity: str = "unknown"
    nas_obsidian_tgz_integrity: str = "unknown"


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


@dataclass(frozen=True)
class ObsidianTarSummary:
    path: str
    verifier_result: str
    direct_inspect_status: str
    exists_non_empty: str
    tar_list_status: str
    required_dirs_status: str
    retained_dirs_seen: List[str]
    forbidden_dirs: List[str]
    missing_required_dirs: List[str]
    result: str
    size_bytes: Optional[int]
    size_warning: Optional[str]
    archive_shape_warning: Optional[str]
    detail: str


class DailyBackupReport:
    """Render a concise daily backup and change report for Telegram."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def render(self) -> str:
        verify = self._parse_verify_log(self.settings.backup_log_root / "nightly_backup_verify_latest.log")
        publisher_done_ts = self._parse_publisher_done_timestamp(self.settings.backup_log_root / "atlas_icloud_publisher.log")
        obsidian_tgz = self.summarize_obsidian_kb_tgz(verify)
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

        obsidian_lines = self._obsidian_tgz_section_lines(obsidian_tgz)
        schema_lines = self._schema_section_lines(schema)
        code_lines = self._code_section_lines(code, backup_script_commits, atlas_commits)
        action_lines = self._action_required_lines(verify, obsidian_tgz)
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
            "3. Obsidian KB Tar Backup",
            *obsidian_lines,
            "",
            "4. DB Schema Changes",
            *schema_lines,
            "",
            "5. Code Changes",
            *code_lines,
            "",
            "6. Action Required",
            *action_lines,
            "",
            "7. Reference Logs",
            *reference_lines,
        ]
        return "\n".join(sections)

    def summarize_obsidian_kb_tgz(self, verify: VerifySummary) -> ObsidianTarSummary:
        tgz_path = verify.nas_obsidian_tgz
        verifier_result = (
            "NAS Obsidian tgz integrity OK" if verify.nas_obsidian_tgz_integrity == "OK" else "NAS Obsidian tgz integrity not confirmed"
        )
        if not tgz_path or tgz_path == "unknown":
            return ObsidianTarSummary(
                path="unknown",
                verifier_result="NAS tgz path not found in verifier log",
                direct_inspect_status="WARN",
                exists_non_empty="unknown",
                tar_list_status="unknown",
                required_dirs_status="unknown",
                retained_dirs_seen=[],
                forbidden_dirs=[],
                missing_required_dirs=[],
                result="WARN",
                size_bytes=None,
                size_warning=None,
                archive_shape_warning=None,
                detail="verifier did not provide NAS tgz path",
            )

        inspection = self._inspect_nas_tgz_via_ssh(tgz_path)
        size_warning = None
        if inspection.size_bytes is not None and inspection.size_bytes < 5 * 1024:
            size_warning = "Obsidian tgz is suspiciously small."
        archive_shape_warning = None
        if inspection.attempted and inspection.verbose_symlink_root:
            archive_shape_warning = "Archive appears to contain only a symlink root entry, not vault contents."

        missing_required: List[str] = []
        retained_seen: List[str] = []
        forbidden_dirs: List[str] = []
        if inspection.attempted and inspection.tar_list_ok:
            missing_required = [name for name in REQUIRED_DIRS if name not in inspection.top_level_dirs]
            retained_seen = [name for name in RETAINED_DIRS if name in inspection.top_level_dirs]
            forbidden_dirs = [name for name in FORBIDDEN_DIRS if name in inspection.top_level_dirs]

        if inspection.attempted:
            exists_non_empty = "OK" if inspection.exists_non_empty else f"FAIL{self._suffix(inspection.exists_detail)}"
            tar_list_status = "OK" if inspection.tar_list_ok else f"FAIL{self._suffix(inspection.tar_list_detail)}"
            if missing_required:
                required_dirs_status = f"WARN — missing {', '.join(missing_required)}"
            else:
                required_dirs_status = f"OK — {', '.join(REQUIRED_DIRS)}"
            direct_inspect_status = "OK"
            if inspection.detail:
                direct_inspect_status += f" — {inspection.detail}"
        else:
            exists_non_empty = "unknown"
            tar_list_status = "unknown"
            required_dirs_status = "unknown"
            direct_inspect_status = f"WARN — {inspection.detail}"

        result = "OK"
        if not inspection.attempted:
            result = "WARN"
        if inspection.attempted and (not inspection.exists_non_empty or not inspection.tar_list_ok):
            result = "FAIL"
        elif missing_required or forbidden_dirs or archive_shape_warning:
            result = "FAIL" if inspection.attempted else "WARN"
        elif size_warning and result == "OK":
            result = "WARN"

        return ObsidianTarSummary(
            path=tgz_path,
            verifier_result=verifier_result,
            direct_inspect_status=direct_inspect_status,
            exists_non_empty=exists_non_empty,
            tar_list_status=tar_list_status,
            required_dirs_status=required_dirs_status,
            retained_dirs_seen=retained_seen,
            forbidden_dirs=forbidden_dirs,
            missing_required_dirs=missing_required,
            result=result,
            size_bytes=inspection.size_bytes,
            size_warning=size_warning,
            archive_shape_warning=archive_shape_warning,
            detail=inspection.detail,
        )

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
            if "NAS Obsidian tgz integrity OK" in line:
                statuses["nas_obsidian_tgz_integrity"] = "OK"
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
            nas_obsidian_tgz=info.get("nas_obsidian_tgz", "unknown"),
            publisher_success_ts=info.get("publisher_success_timestamp", "unknown"),
            main_backup_status=statuses.get("main_backup_status", "unknown"),
            local_run_status=statuses.get("local_run_status", "unknown"),
            nas_run_status=statuses.get("nas_run_status", "unknown"),
            postgres_dump_sanity=statuses.get("postgres_dump_sanity", "unknown"),
            tools_backup_sanity=statuses.get("tools_backup_sanity", "unknown"),
            icloud_publisher_sanity=statuses.get("icloud_publisher_sanity", "unknown"),
            nas_obsidian_tgz_integrity=statuses.get("nas_obsidian_tgz_integrity", "unknown"),
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

    def _obsidian_tgz_section_lines(self, summary: ObsidianTarSummary) -> List[str]:
        lines = [f"- NAS tgz: {summary.path}"]
        lines.append(f"- Verifier result: {summary.verifier_result}")
        if summary.size_bytes is not None:
            lines.append(f"- Size: {summary.size_bytes} bytes")
        lines.append(f"- Exists / non-empty: {summary.exists_non_empty}")
        lines.append(f"- tar list: {summary.tar_list_status}")
        lines.append(f"- Required dirs: {summary.required_dirs_status}")
        if summary.retained_dirs_seen:
            retained = ", ".join(summary.retained_dirs_seen[:8])
        elif summary.direct_inspect_status.startswith("OK"):
            retained = "none"
        else:
            retained = "unknown"
        lines.append(f"- Retained dirs seen: {retained}")
        forbidden = ", ".join(summary.forbidden_dirs) if summary.forbidden_dirs else "none"
        lines.append(f"- Forbidden working dirs: {forbidden}")
        if summary.size_warning:
            lines.append(f"- Size sanity: WARN — {summary.size_warning}")
        if summary.archive_shape_warning:
            lines.append(f"- Archive shape: FAIL — {summary.archive_shape_warning}")
        lines.append(f"- Direct inspect: {summary.direct_inspect_status}")
        lines.append(f"- Result: {summary.result}")
        return lines[:11]

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

    def _action_required_lines(self, verify: VerifySummary, obsidian_tgz: ObsidianTarSummary) -> List[str]:
        actions: List[str] = []
        if verify.final_status != "OK":
            actions.append(f"- Investigate FINAL_STATUS={verify.final_status}.")
        if verify.warn_count != "0":
            actions.append(f"- Clear WARN_COUNT={verify.warn_count}.")
        if verify.error_count != "0":
            actions.append(f"- Clear ERROR_COUNT={verify.error_count}.")
        if obsidian_tgz.result != "OK":
            detail_parts = []
            if obsidian_tgz.missing_required_dirs:
                detail_parts.append(f"missing required dirs: {', '.join(obsidian_tgz.missing_required_dirs)}")
            if obsidian_tgz.forbidden_dirs:
                detail_parts.append(f"forbidden dirs: {', '.join(obsidian_tgz.forbidden_dirs)}")
            if obsidian_tgz.tar_list_status.startswith("FAIL"):
                detail_parts.append("tar list failed")
            if obsidian_tgz.exists_non_empty.startswith("FAIL"):
                detail_parts.append("archive missing or empty")
            if obsidian_tgz.size_warning:
                detail_parts.append(obsidian_tgz.size_warning)
            if obsidian_tgz.archive_shape_warning:
                detail_parts.append(obsidian_tgz.archive_shape_warning)
            if not detail_parts and obsidian_tgz.direct_inspect_status.startswith("WARN"):
                detail_parts.append("direct inspect unavailable; based on verifier only")
            if not detail_parts:
                detail_parts.append(obsidian_tgz.detail)
            actions.append(f"- Check Obsidian KB NAS tgz: {obsidian_tgz.path} ({'; '.join(part for part in detail_parts if part)})")
        if not actions:
            return ["- None"]
        actions.insert(0, f"- Review verifier log: {self.settings.backup_log_root / 'nightly_backup_verify_latest.log'}")
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

    def _inspect_nas_tgz_via_ssh(self, tgz_path: str) -> "_SshInspectionResult":
        remote_script = (
            f'path="{tgz_path}"; '
            'if [ ! -s "$path" ]; then printf "EXISTS=0\\n"; exit 0; fi; '
            'printf "EXISTS=1\\n"; '
            'wc -c < "$path" | awk \'{print "SIZE="$1}\'; '
            'if tar -tzf "$path" >/tmp/knowledge_bot_tar_list.$$ 2>/tmp/knowledge_bot_tar_err.$$; then '
            'printf "TAR_OK=1\\n"; '
            'head -200 /tmp/knowledge_bot_tar_list.$$; '
            'printf "VERBOSE_BEGIN\\n"; '
            'tar -tzvf "$path" | head -20; '
            'else printf "TAR_OK=0\\n"; cat /tmp/knowledge_bot_tar_err.$$; fi; '
            'rm -f /tmp/knowledge_bot_tar_list.$$ /tmp/knowledge_bot_tar_err.$$'
        )
        cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            NAS_SSH_TARGET,
            remote_script,
        ]
        try:
            result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=12, check=False)
        except Exception as exc:
            return _SshInspectionResult(False, False, False, None, set(), f"SSH unavailable: {exc}", "", "")
        if result.returncode != 0 and not result.stdout.strip():
            detail = result.stderr.strip() or f"ssh exit {result.returncode}"
            return _SshInspectionResult(False, False, False, None, set(), f"SSH unavailable: {detail}", result.stdout, result.stderr)

        exists_non_empty = "EXISTS=1" in result.stdout
        tar_ok = "TAR_OK=1" in result.stdout
        size_bytes = self._parse_size_line(result.stdout)
        top_level_dirs = self._parse_top_level_dirs(result.stdout.splitlines())

        verbose_symlink_root = self._has_verbose_symlink_root(result.stdout.splitlines())

        if not exists_non_empty:
            return _SshInspectionResult(True, False, False, size_bytes, top_level_dirs, "remote file missing or empty", result.stdout, result.stderr)
        if not tar_ok:
            tar_detail = self._tar_error_detail(result.stdout, result.stderr)
            return _SshInspectionResult(True, True, False, size_bytes, top_level_dirs, tar_detail, result.stdout, result.stderr, verbose_symlink_root)
        return _SshInspectionResult(True, True, True, size_bytes, top_level_dirs, "SSH inspection passed", result.stdout, result.stderr, verbose_symlink_root)

    def _parse_size_line(self, text: str) -> Optional[int]:
        for raw in text.splitlines():
            if raw.startswith("SIZE="):
                try:
                    return int(raw.split("=", 1)[1].strip())
                except ValueError:
                    return None
        return None

    def _parse_top_level_dirs(self, lines: Sequence[str]) -> set[str]:
        names: set[str] = set()
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith(("EXISTS=", "SIZE=", "TAR_OK=", "VERBOSE_BEGIN")):
                continue
            if re.match(r"^[bcdlps-][rwx-]{9}\s", line):
                continue
            parts = PurePosixPath(line.rstrip("/")).parts
            if not parts:
                continue
            top = parts[0]
            if top == "atlas" and len(parts) > 1:
                names.add(parts[1])
            else:
                names.add(top)
        return names

    def _tar_error_detail(self, stdout: str, stderr: str) -> str:
        merged = "\n".join(part for part in [stdout, stderr] if part).splitlines()
        for line in merged:
            if line.startswith(("EXISTS=", "SIZE=", "TAR_OK=", "VERBOSE_BEGIN")) or not line.strip():
                continue
            return f"tar list failed: {line.strip()[:180]}"
        return "tar list failed"

    def _has_verbose_symlink_root(self, lines: Sequence[str]) -> bool:
        for raw in lines:
            line = raw.strip()
            if not line.startswith("l"):
                continue
            if " atlas -> " in line or line.endswith(" atlas"):
                return True
        return False

    def _suffix(self, detail: str) -> str:
        return f" — {detail}" if detail else ""


@dataclass(frozen=True)
class _SshInspectionResult:
    attempted: bool
    exists_non_empty: bool
    tar_list_ok: bool
    size_bytes: Optional[int]
    top_level_dirs: set[str]
    detail: str
    stdout: str
    stderr: str
    verbose_symlink_root: bool = False

    @property
    def exists_detail(self) -> str:
        return self.detail if not self.exists_non_empty else ""

    @property
    def tar_list_detail(self) -> str:
        return self.detail if not self.tar_list_ok else ""
