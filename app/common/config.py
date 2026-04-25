from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


CANONICAL_ENV_CONTRACT: Dict[str, Dict[str, str]] = {
    "GITHUB_TOKEN": {"required": "no", "category": "atlas_readonly", "note": "optional token fallback; SSH/local repo does not require it"},
    "GITHUB_REPO_URL": {"required": "yes", "category": "atlas_readonly", "note": "Atlas Repo A remote identity"},
    "GITHUB_DEFAULT_BRANCH": {"required": "yes", "category": "atlas_readonly", "note": "Atlas Repo A branch identity"},
    "GITHUB_BOT_REPO_URL": {"required": "yes", "category": "bot_repo", "note": "Repo B remote identity"},
    "GITHUB_BOT_BRANCH": {"required": "yes", "category": "bot_repo", "note": "Repo B branch identity"},
    "KB_TELEGRAM_BOT_TOKEN": {"required": "yes_for_bot", "category": "telegram", "note": "required only when starting Telegram polling"},
    "KB_ALLOWED_CHAT_IDS": {"required": "no", "category": "telegram", "note": "optional comma-separated allowlist"},
    "OPENAI_MODEL_COMPLEX": {"required": "reserved", "category": "codex_openai", "note": "reserved for future Codex handoff"},
    "OPENAI_MODEL_NORMAL": {"required": "reserved", "category": "codex_openai", "note": "reserved for future Codex handoff"},
    "OPENAI_REASONING_EFFORT": {"required": "reserved", "category": "codex_openai", "note": "reserved for future Codex handoff"},
    "KB_KNOWLEDGE_VAULT_ROOT": {"required": "yes", "category": "knowledge_roots", "note": "curated Obsidian vault"},
    "KB_KNOWLEDGE_LOCAL_ROOT": {"required": "yes", "category": "knowledge_roots", "note": "local source/work layer"},
    "KB_CHATGPT_EXPORT_ROOT": {"required": "yes", "category": "knowledge_roots", "note": "ChatGPT raw source root"},
    "KB_ATLAS_REPO_PATH": {"required": "yes", "category": "cache", "note": "Repo A local readonly path"},
    "KB_GITHUB_CACHE_ROOT": {"required": "yes", "category": "cache", "note": "shared committed-code cache root"},
    "KB_KEEP_COMMIT_SNAPSHOTS": {"required": "no", "category": "cache", "note": "retain commit-pinned snapshots"},
    "KB_ALLOW_ATLAS_READONLY_FALLBACK": {"required": "no", "category": "cache", "note": "allow read-only fallback if cache cannot verify"},
    "KB_STATE_ROOT": {"required": "yes", "category": "state_logs", "note": "bot state root"},
    "KB_REVIEW_OUTPUT_ROOT": {"required": "yes", "category": "state_logs", "note": "review output root"},
    "KB_LOG_ROOT": {"required": "yes", "category": "state_logs", "note": "bot log root"},
    "KB_NAS_ARCHIVE_ROOT": {"required": "no", "category": "archive", "note": "reserved optional archive root"},
}


def load_env_file(path: Path) -> Dict[str, str]:
    """Load a simple KEY=VALUE env file without overriding real env vars."""
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _get(name: str, default: str, env_file: Dict[str, str]) -> str:
    return os.environ.get(name) or env_file.get(name) or default


def _bool(name: str, default: bool, env_file: Dict[str, str]) -> bool:
    raw = _get(name, "1" if default else "0", env_file).strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _list(name: str, env_file: Dict[str, str]) -> List[str]:
    raw = _get(name, "", env_file)
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass(frozen=True)
class Settings:
    """Runtime path, repo identity, and behavior contract for knowledge-bot."""

    project_root: Path
    github_token: Optional[str]
    github_repo_url: str
    github_default_branch: str
    github_bot_repo_url: str
    github_bot_branch: str
    openai_model_complex: str
    openai_model_normal: str
    openai_reasoning_effort: str
    knowledge_vault_root: Path
    knowledge_local_root: Path
    chatgpt_export_root: Path
    atlas_repo_path: Path
    github_cache_root: Path
    state_root: Path
    review_output_root: Path
    log_root: Path
    nas_archive_root: Path
    telegram_bot_token: Optional[str]
    allowed_chat_ids: List[str]
    keep_commit_snapshots: bool
    allow_atlas_readonly_fallback: bool

    def ensure_runtime_dirs(self) -> None:
        """Create writable runtime directories owned by this bot."""
        for path in [
            self.github_cache_root,
            self.state_root,
            self.review_output_root,
            self.log_root,
        ]:
            path.mkdir(parents=True, exist_ok=True)


def load_settings(env_path: Optional[Path] = None) -> Settings:
    """Load settings from .env and process environment."""
    project_root = Path(__file__).resolve().parents[2]
    env_file = load_env_file(env_path or project_root / ".env")
    knowledge_local = Path(_get(
        "KB_KNOWLEDGE_LOCAL_ROOT",
        "/Users/liuteli/infra/knowledge_local/obsidian-main/atlas",
        env_file,
    ))
    state_root = Path(_get(
        "KB_STATE_ROOT",
        str(knowledge_local / "99_SYSTEM/jobs/knowledge-bot"),
        env_file,
    ))
    return Settings(
        project_root=project_root,
        github_token=_get("GITHUB_TOKEN", "", env_file) or None,
        github_repo_url=_get("GITHUB_REPO_URL", "https://github.com/liuteli/atlas.git", env_file),
        github_default_branch=_get("GITHUB_DEFAULT_BRANCH", "main", env_file),
        github_bot_repo_url=_get("GITHUB_BOT_REPO_URL", "https://github.com/liuteli/atlas_kb_bot.git", env_file),
        github_bot_branch=_get("GITHUB_BOT_BRANCH", "main", env_file),
        openai_model_complex=_get("OPENAI_MODEL_COMPLEX", "gpt-5", env_file),
        openai_model_normal=_get("OPENAI_MODEL_NORMAL", "gpt-5", env_file),
        openai_reasoning_effort=_get("OPENAI_REASONING_EFFORT", "high", env_file),
        knowledge_vault_root=Path(_get(
            "KB_KNOWLEDGE_VAULT_ROOT",
            "/Users/liuteli/infra/knowledge/obsidian-main/atlas",
            env_file,
        )),
        knowledge_local_root=knowledge_local,
        chatgpt_export_root=Path(_get(
            "KB_CHATGPT_EXPORT_ROOT",
            str(knowledge_local / "10_SOURCES_RAW/chatgpt-export"),
            env_file,
        )),
        atlas_repo_path=Path(_get(
            "KB_ATLAS_REPO_PATH",
            "/Users/liuteli/infra/docker/postgres/atlas",
            env_file,
        )),
        github_cache_root=Path(_get(
            "KB_GITHUB_CACHE_ROOT",
            str(knowledge_local / "12_MIRRORS/github-committed-cache"),
            env_file,
        )),
        state_root=state_root,
        review_output_root=Path(_get(
            "KB_REVIEW_OUTPUT_ROOT",
            str(state_root / "reviews"),
            env_file,
        )),
        log_root=Path(_get(
            "KB_LOG_ROOT",
            "/Users/liuteli/infra/logs/knowledge-bot",
            env_file,
        )),
        nas_archive_root=Path(_get(
            "KB_NAS_ARCHIVE_ROOT",
            "/Users/liuteli/nas/datasets/knowledge",
            env_file,
        )),
        telegram_bot_token=_get("KB_TELEGRAM_BOT_TOKEN", "", env_file) or None,
        allowed_chat_ids=_list("KB_ALLOWED_CHAT_IDS", env_file),
        keep_commit_snapshots=_bool("KB_KEEP_COMMIT_SNAPSHOTS", True, env_file),
        allow_atlas_readonly_fallback=_bool("KB_ALLOW_ATLAS_READONLY_FALLBACK", True, env_file),
    )
