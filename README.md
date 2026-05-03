# knowledge-bot

`knowledge-bot` is an independent knowledge ingestion, Telegram review, and Codex handoff system for Atlas knowledge sources.

## Repo Boundary

Repo B is the bot repository:

- Local path: `/Users/liuteli/infra/docker/knowledge-bot`
- GitHub: `https://github.com/liuteli/atlas_kb_bot.git`
- Branch contract: `main`

Repo A is the Atlas read-only reference repository:

- Local path: `/Users/liuteli/infra/docker/postgres/atlas`
- GitHub: `https://github.com/liuteli/atlas.git`
- Branch contract: `main`
- Safety rule: never write, stage, commit, move, delete, or patch files in Repo A from this bot.

## GitHub Auth Policy

In the normal local Repo A + SSH remote setup, `GITHUB_TOKEN` is not required.
The committed-code cache reads Repo A's committed tree with `git archive HEAD`.
`GITHUB_TOKEN` is reserved only for a future token-based fallback if SSH/local repo access is unavailable.

## Environment

Runtime configuration is read from `.env`. `.env.example` is only a template contract and is never used as live configuration.

```bash
cp .env.example .env
chmod 600 .env
```

Required to run Telegram polling:

- `KB_TELEGRAM_BOT_TOKEN`

Optional but recommended after `/whoami`:

- `KB_ALLOWED_CHAT_IDS`

Reserved for future Codex/OpenAI handoff:

- `OPENAI_MODEL_COMPLEX`
- `OPENAI_MODEL_NORMAL`
- `OPENAI_REASONING_EFFORT`

## Docker Startup

This host currently supports legacy Compose:

```bash
docker-compose config
docker-compose up -d --build --force-recreate knowledge-bot
docker-compose ps
docker-compose logs --tail=200
```

The container runs with `TZ=Asia/Singapore` and mounts `.env` read-only at `/app/.env`.
The backup report feature also needs a read-only mount for `/Users/liuteli/infra/backups`.
The application source is baked into the Docker image and `docker-compose.yml` does not bind-mount the repo into `/app`.
After any `app/...` code change, redeploy with:

```bash
docker compose up -d --build --force-recreate knowledge-bot
```

A plain `docker compose restart knowledge-bot` is only for env/config/runtime-only restarts and is not a code deployment.

## CLI Commands

```bash
python3 -m app.cli init-dirs
python3 -m app.cli env-audit
python3 -m app.cli repo-boundary-audit
python3 -m app.cli cache-refresh --repo atlas --dry-run
python3 -m app.cli detect
python3 -m app.cli review <source_id>
python3 -m app.cli archive-source <source_id>
python3 -m app.cli archive-external-review-pack <pack_dir>
python3 -m app.cli status
python3 -m app.cli backup-report --dry-run
python3 -m app.cli bot --dry-run
python3 -m app.cli bot
```

Production dry-run from the running container:

```bash
docker compose exec -T knowledge-bot python3 -m app.cli backup-report --dry-run
```

## Telegram Commands

- `/whoami`: return `chat_id`, `user_id`, chat type, and username/title when available.
- `/sources`: list detected ChatGPT sources.
- `/review <id>`: run review-only audit.
- `/status`: list recent review runs.
- `/backup_report`: render and send the daily backup and change report immediately.
- `/backup_report` includes the Obsidian KB NAS tgz path plus tar integrity and curated-vault completeness summary.

## Source Lifecycles

Raw source lifecycle:

```text
detect -> review -> archive-source
```

- `archive-source` marks the raw source manifest state as archived.
- `archive-source` does not delete or move raw ChatGPT export files.

External Codex review pack lifecycle:

```text
21_STAGING/chat-history-review/<pack>
-> archive-external-review-pack
-> 11_SOURCES_CLEAN/chat-history-review/completed/<pack>
```

- This is a new explicit convention for external Codex-generated review packs.
- These packs are not Telegram `/review` outputs.
- `archive-external-review-pack` only closes the external review pack lifecycle.
- `archive-external-review-pack` does not edit curated Obsidian.
- `archive-external-review-pack` does not alter the raw-source manifest.
- Raw chat must not be wholesale-ingested into Obsidian.
- Active staging should not retain completed external review packs.

## Daily Backup Report

The existing long-polling Telegram bot sends a daily backup report at `08:05` Asia/Singapore from inside the bot process. It does not use `launchd`, cron, or a separate scheduler.

Automatic sends go to all `KB_ALLOWED_CHAT_IDS`. If the bot was offline at `08:05`, it sends once on the next scheduler check after `08:05` and before the same-day cutoff at `12:00`.

The report is built from read-only sources:

- `/Users/liuteli/infra/backups/logs/nightly_backup_verify_latest.log`
- `/Users/liuteli/infra/backups/logs/atlas_icloud_publisher.log`
- `KB_DB_SCHEMA_DIFF_ROOT`
- `KB_GITHUB_DIFF_ROOT`
- recent git logs from `KB_BACKUP_SCRIPTS_ROOT` and `KB_ATLAS_REPO_PATH`

The daily report also summarizes the NAS Obsidian KB tar backup location, whether the tgz is non-empty, whether `tar -tzf` succeeds, whether required curated-vault directories are present, and whether forbidden working/export directories leaked into the archive.
Publisher success is keyed from `atlas-icloud-publisher publish-db-schema done`, and Obsidian staging success is keyed from `stage obsidian vault done duration_ms=... result=ok`.
The active vault source contract for that report is `/Users/liuteli/Library/Mobile Documents/iCloud~md~obsidian/Documents/atlas`, staged locally to `/Users/liuteli/infra/backups/staging/obsidian-atlas/atlas` before NAS tar creation.
The retired historical path `/Users/liuteli/Library/Mobile Documents/com~apple~CloudDocs/Obsidian/atlas` should not be treated as the live vault.
Verifier output is the source of truth for the Obsidian KB tar result. If verifier confirms real curated vault content, a direct SSH inspect being skipped or unavailable is informational and should not downgrade the report to `WARN`.
The recent failure mode was host code patched without rebuilding the container image; rebuilding and force-recreating `knowledge-bot` fixed the report output.

Daily backup report env vars:

- `KB_DAILY_BACKUP_REPORT_ENABLED=1`
- `KB_DAILY_BACKUP_REPORT_TIME=08:05`
- `KB_DAILY_BACKUP_REPORT_CUTOFF=12:00`
- `KB_BACKUP_LOG_ROOT=/Users/liuteli/infra/backups/logs`
- `KB_BACKUP_SCRIPTS_ROOT=/Users/liuteli/infra/backups/scripts`
- `KB_DB_SCHEMA_DIFF_ROOT=/Users/liuteli/infra/knowledge_local/obsidian-main/atlas/21_STAGING/db-schema-diffs`
- `KB_GITHUB_DIFF_ROOT=/Users/liuteli/infra/knowledge_local/obsidian-main/atlas/21_STAGING/github-diffs/atlas`

Use `/whoami` first, copy `chat_id`, then set:

```env
KB_ALLOWED_CHAT_IDS=<chat_id>
```

## Logger Contract

Runtime logs are written to `/Users/liuteli/infra/logs/knowledge-bot/YYYY-MM-DD.log` using Singapore calendar days.

Each command logs received and success/failure events with bounded structured fields:

- `ts`
- `level`
- `user_id`
- `chat_id`
- `username`
- `event`
- `command`
- `source_id`
- `review_id`
- `status`
- `summary`

Tokens, secrets, raw prompts, and raw chat history bodies must never be logged.

## Review Output Contract

Each review writes exactly six core files:

- `summary.md`
- `extracted_knowledge_inventory.md`
- `wiki_coverage_matrix.md`
- `code_consistency_audit.md`
- `backfill_shortlist.md`
- `applied_mapping_or_apply_plan.md`

External Codex-generated chat-history review packs are separate artifacts under `21_STAGING/chat-history-review/`.
They are closed into `11_SOURCES_CLEAN/chat-history-review/completed/` with `python3 -m app.cli archive-external-review-pack <pack_dir>`.
This command archives the external pack directory and any matching staging-root session note, writes a `_STATUS.md` file, and leaves raw source files plus `chatgpt_sources.json` unchanged.

## Backup Contract

Code is protected by git and GitHub, and is also included in the existing Docker config backup because it lives under `/Users/liuteli/infra/docker`.

Current nightly backup does not clearly include all mutable knowledge-bot runtime assets:

- `/Users/liuteli/infra/logs/knowledge-bot`
- `KB_STATE_ROOT`
- `KB_REVIEW_OUTPUT_ROOT`

The minimal close-out script is:

```bash
scripts/backup_knowledge_bot_state.sh --dry-run
scripts/backup_knowledge_bot_state.sh
```

This script creates a compact manual bundle for logs, state, and review outputs. Wiring it into the global nightly backup remains a small follow-up and should not change the broader backup framework without a bounded review.
