from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.bot.telegram_bot import TelegramReviewBot
from app.common.committed_cache import CommittedCodeCache
from app.common.config import CANONICAL_ENV_CONTRACT, load_env_file, load_settings
from app.common.logging_utils import log_event, setup_logger
from app.common.subprocess_utils import run_cmd
from app.ingest.audit_runner import ChatHistoryReviewRunner
from app.ingest.chatgpt_detector import ChatGPTSourceDetector, short_source_id


def _env_keys(path: Path) -> list[str]:
    if not path.exists():
        return []
    return sorted(load_env_file(path).keys())


def main() -> None:
    parser = argparse.ArgumentParser(prog="knowledge-bot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-dirs")

    cache = sub.add_parser("cache-refresh")
    cache.add_argument("--repo", default="atlas")
    cache.add_argument("--dry-run", action="store_true")

    detect = sub.add_parser("detect")
    detect.add_argument("--json", action="store_true")

    review = sub.add_parser("review")
    review.add_argument("source_id_or_path")

    archive = sub.add_parser("archive-source")
    archive.add_argument("source_ids", nargs="+")
    archive.add_argument("--reason", default="manual_completed_by_user")
    archive.add_argument("--operator", default="codex")

    sub.add_parser("status")
    sub.add_parser("env-audit")
    sub.add_parser("repo-boundary-audit")

    bot = sub.add_parser("bot")
    bot.add_argument("--dry-run", action="store_true", help="validate config and handlers without polling Telegram")

    args = parser.parse_args()
    settings = load_settings(Path(".env") if Path(".env").exists() else None)

    if args.cmd == "init-dirs":
        settings.ensure_runtime_dirs()
        print(f"created runtime dirs under {settings.state_root}")
        return

    if args.cmd == "cache-refresh":
        if args.repo != "atlas":
            raise SystemExit("Phase 1 supports only --repo atlas")
        result = CommittedCodeCache(settings.github_cache_root).refresh(
            "atlas",
            settings.atlas_repo_path,
            repo_url=settings.github_repo_url,
            branch=settings.github_default_branch,
            keep_commit_snapshot=settings.keep_commit_snapshots,
            dry_run=args.dry_run,
        )
        print(json.dumps(result.__dict__, default=str, ensure_ascii=False, indent=2))
        return

    if args.cmd == "detect":
        settings.ensure_runtime_dirs()
        detector = ChatGPTSourceDetector(settings.chatgpt_export_root, settings.state_root / "chatgpt_sources.json")
        records = detector.scan_and_update()
        data = [record.__dict__ for record in records]
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            for record in records:
                print(f"{short_source_id(record.source_id)}\t{record.source_id}\t{record.source_type}\t{record.display_name}\t{record.rough_summary}\t/review {short_source_id(record.source_id)}")
        return

    if args.cmd == "archive-source":
        settings.ensure_runtime_dirs()
        detector = ChatGPTSourceDetector(settings.chatgpt_export_root, settings.state_root / "chatgpt_sources.json")
        archived = detector.archive_sources(args.source_ids, reason=args.reason, operator=args.operator)
        logger = setup_logger("knowledge_bot_sources", settings.log_root)
        for event in archived:
            log_event(
                logger,
                event="source_archived",
                source_id=event.get("source_id", ""),
                status="archived",
                summary=f"reason={args.reason} operator={args.operator}",
            )
        print(json.dumps({"archived": archived}, ensure_ascii=False, indent=2))
        return

    if args.cmd == "review":
        result = ChatHistoryReviewRunner(settings).review(args.source_id_or_path)
        print(f"review complete: {result.output_dir}")
        print(f"summary: {result.summary_path}")
        print(f"fallback_used: {result.fallback_used}")
        if result.fallback_reason:
            print(f"fallback_reason: {result.fallback_reason}")
        return

    if args.cmd == "status":
        path = settings.state_root / "review_runs.json"
        if not path.exists():
            print("no review runs")
            return
        obj = json.loads(path.read_text(encoding="utf-8"))
        for run in (obj.get("runs") or [])[-20:]:
            print(f"{run.get('created_at_utc')}\t{run.get('source_id')}\t{run.get('output_dir')}")
        return

    if args.cmd == "env-audit":
        canonical = set(CANONICAL_ENV_CONTRACT.keys())
        env_path = settings.project_root / ".env"
        example_path = settings.project_root / ".env.example"
        env_keys = set(_env_keys(env_path))
        example_keys = set(_env_keys(example_path))
        print(json.dumps({
            "canonical_env_count": len(canonical),
            "env_present": env_path.exists(),
            "env_key_count": len(env_keys),
            "env_missing_keys": sorted(canonical - env_keys),
            "env_extra_keys": sorted(env_keys - canonical),
            "env_example_present": example_path.exists(),
            "env_example_key_count": len(example_keys),
            "env_example_missing_keys": sorted(canonical - example_keys),
            "env_example_extra_keys": sorted(example_keys - canonical),
            "canonical_env_names": sorted(canonical),
            "token_values_redacted": True,
        }, ensure_ascii=False, indent=2))
        return

    if args.cmd == "repo-boundary-audit":
        atlas_branch = run_cmd(["git", "-C", str(settings.atlas_repo_path), "rev-parse", "--abbrev-ref", "HEAD"])
        atlas_head = run_cmd(["git", "-C", str(settings.atlas_repo_path), "rev-parse", "HEAD"])
        atlas_status = run_cmd(["git", "-C", str(settings.atlas_repo_path), "status", "--short"])
        bot_git = run_cmd(["git", "-C", str(settings.project_root), "rev-parse", "--is-inside-work-tree"])
        bot_branch = run_cmd(["git", "-C", str(settings.project_root), "rev-parse", "--abbrev-ref", "HEAD"])
        print(json.dumps({
            "repo_a_atlas": {
                "path": str(settings.atlas_repo_path),
                "role": "readonly_reference",
                "repo_url": settings.github_repo_url,
                "default_branch_contract": settings.github_default_branch,
                "actual_branch": atlas_branch.stdout.strip() if atlas_branch.returncode == 0 else None,
                "head": atlas_head.stdout.strip() if atlas_head.returncode == 0 else None,
                "dirty_status_observed_readonly": atlas_status.stdout.strip() if atlas_status.returncode == 0 else "",
                "writes_allowed": False,
            },
            "repo_b_bot": {
                "path": str(settings.project_root),
                "role": "development_target",
                "repo_url": settings.github_bot_repo_url,
                "branch_contract": settings.github_bot_branch,
                "actual_branch": bot_branch.stdout.strip() if bot_branch.returncode == 0 else None,
                "is_git_repo": bot_git.returncode == 0,
                "writes_allowed": True,
            },
            "github_token_required_for_local_ssh_mode": False,
        }, ensure_ascii=False, indent=2))
        return

    if args.cmd == "bot":
        if args.dry_run:
            print(json.dumps({
                "bot_startup_preflight": "ok",
                "telegram_token_configured": bool(settings.telegram_bot_token),
                "allowed_chat_ids_count": len(settings.allowed_chat_ids),
                "registered_commands": TelegramReviewBot.registered_commands(),
                "log_root": str(settings.log_root),
            }, ensure_ascii=False, indent=2))
            return
        TelegramReviewBot(settings).run_forever()
        return


if __name__ == "__main__":
    main()
