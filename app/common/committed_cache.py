from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from app.common.subprocess_utils import require_success, run_cmd


@dataclass
class CacheRefreshResult:
    """Result of one committed-code cache refresh attempt."""

    repo: str
    repo_url: str
    branch: str
    source_repo: Path
    commit: str
    latest_path: Path
    manifest_path: Path
    source_mode: str
    updated: bool
    dry_run: bool
    previous_commit: Optional[str]
    dirty_status: str


class CommittedCodeCache:
    """Maintain committed-only source snapshots shared by ingest workflows.

    Source priority:
    1. Local Repo A committed tree via `git archive HEAD`.
    2. Future SSH remote snapshot support if local Repo A is unavailable.
    3. Future optional token fallback.

    Phase 1 intentionally does not mutate Repo A and does not require
    GITHUB_TOKEN when the local SSH-backed Atlas repo is available.
    """

    def __init__(self, cache_root: Path) -> None:
        self.cache_root = cache_root

    def _repo_root(self, repo: str) -> Path:
        return self.cache_root / repo

    def latest_path(self, repo: str) -> Path:
        return self._repo_root(repo) / "latest"

    def manifest_path(self, repo: str) -> Path:
        return self._repo_root(repo) / "manifests" / "latest.json"

    def current_commit(self, repo_path: Path) -> str:
        result = run_cmd(["git", "-C", str(repo_path), "rev-parse", "HEAD"])
        return require_success(result, "git rev-parse HEAD")

    def current_branch(self, repo_path: Path) -> str:
        result = run_cmd(["git", "-C", str(repo_path), "rev-parse", "--abbrev-ref", "HEAD"])
        return require_success(result, "git rev-parse --abbrev-ref HEAD")

    def dirty_status(self, repo_path: Path) -> str:
        result = run_cmd(["git", "-C", str(repo_path), "status", "--short"])
        if result.returncode != 0:
            return f"status_error:{result.stderr.strip()}"
        return result.stdout.strip()

    def read_latest_manifest(self, repo: str) -> Dict[str, object]:
        path = self.manifest_path(repo)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def refresh(
        self,
        repo: str,
        repo_path: Path,
        repo_url: str,
        branch: str,
        keep_commit_snapshot: bool = True,
        dry_run: bool = False,
    ) -> CacheRefreshResult:
        """Refresh latest cache when Repo A HEAD commit changed."""
        if not repo_path.exists():
            raise RuntimeError(
                "local Atlas Repo A is required in Phase 1; SSH/token remote snapshot fallback is reserved"
            )
        commit = self.current_commit(repo_path)
        actual_branch = self.current_branch(repo_path)
        dirty = self.dirty_status(repo_path)
        manifest = self.read_latest_manifest(repo)
        previous_commit = manifest.get("commit") if manifest else None
        latest = self.latest_path(repo)
        manifest_path = self.manifest_path(repo)
        source_mode = "local_repo_committed_tree"

        if previous_commit == commit and latest.exists():
            return CacheRefreshResult(repo, repo_url, actual_branch or branch, repo_path, commit, latest, manifest_path, source_mode, False, dry_run, str(previous_commit), dirty)

        if dry_run:
            return CacheRefreshResult(repo, repo_url, actual_branch or branch, repo_path, commit, latest, manifest_path, source_mode, previous_commit != commit, True, str(previous_commit) if previous_commit else None, dirty)

        repo_root = self._repo_root(repo)
        commits_root = repo_root / "commits"
        manifests_root = repo_root / "manifests"
        repo_root.mkdir(parents=True, exist_ok=True)
        commits_root.mkdir(parents=True, exist_ok=True)
        manifests_root.mkdir(parents=True, exist_ok=True)

        commit_snapshot = commits_root / commit
        if not commit_snapshot.exists():
            with tempfile.TemporaryDirectory(prefix=f"{repo}-{commit}-") as td:
                temp_dir = Path(td)
                archive_path = temp_dir / "snapshot.tar"
                archive_result = run_cmd([
                    "git", "-C", str(repo_path), "archive",
                    "--format=tar", f"--output={archive_path}", "HEAD",
                ])
                require_success(archive_result, "git archive HEAD")
                extract_dir = temp_dir / "extract"
                extract_dir.mkdir()
                with tarfile.open(archive_path) as tar:
                    self._safe_extract(tar, extract_dir)
                self._write_snapshot_manifest(
                    extract_dir,
                    repo=repo,
                    repo_url=repo_url,
                    branch=actual_branch or branch,
                    repo_path=repo_path,
                    commit=commit,
                    dirty=dirty,
                    source_mode=source_mode,
                )
                shutil.copytree(extract_dir, commit_snapshot)

        if latest.exists():
            backup = repo_root / f"latest.previous.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            shutil.move(str(latest), str(backup))
        shutil.copytree(commit_snapshot, latest)
        latest_manifest = latest / ".knowledge_cache_manifest.json"
        shutil.copy2(latest_manifest, manifest_path)
        if not keep_commit_snapshot and commit_snapshot.exists():
            shutil.rmtree(commit_snapshot)
        return CacheRefreshResult(repo, repo_url, actual_branch or branch, repo_path, commit, latest, manifest_path, source_mode, True, False, str(previous_commit) if previous_commit else None, dirty)

    def _safe_extract(self, tar: tarfile.TarFile, target: Path) -> None:
        """Extract a tar archive while rejecting path traversal entries."""
        target_resolved = target.resolve()
        for member in tar.getmembers():
            member_path = (target / member.name).resolve()
            if not str(member_path).startswith(str(target_resolved)):
                raise RuntimeError(f"unsafe archive path: {member.name}")
        tar.extractall(target)

    def _write_snapshot_manifest(
        self,
        root: Path,
        repo: str,
        repo_url: str,
        branch: str,
        repo_path: Path,
        commit: str,
        dirty: str,
        source_mode: str,
    ) -> None:
        manifest = {
            "repo_identity": repo,
            "repo_url": repo_url,
            "branch": branch,
            "source_repo": str(repo_path),
            "commit": commit,
            "snapshot_time_utc": datetime.now(timezone.utc).isoformat(),
            "source_dirty_status_at_cache_time": dirty,
            "source_mode": source_mode,
            "source_priority": [
                "local_repo_committed_tree",
                "ssh_remote_snapshot_reserved",
                "token_fallback_reserved",
            ],
            "snapshot_method": "git archive HEAD",
            "committed_only": True,
            "github_token_required": False,
        }
        (root / ".knowledge_cache_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
