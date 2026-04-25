# AGENTS.md

## Scope

This file governs `/Users/liuteli/infra/docker/knowledge-bot`.

## Repo Boundary

- Repo A, Atlas: `/Users/liuteli/infra/docker/postgres/atlas`
- Repo A role: read-only committed-code/reference source only.
- Repo A forbidden actions: write files, apply patches, `git add`, `git commit`, `git push`, `mv`, `rm`, rename, delete, or modify config.
- Repo B, atlas_kb_bot: `/Users/liuteli/infra/docker/knowledge-bot`
- Repo B role: knowledge-bot application, docs, tests, Docker, Telegram source-ingest orchestration.
- Repo B may be edited, tested, committed, and pushed when explicitly requested.

## Python Command Safety

Within Repo B, `python3 ...` commands that are read-only, validation-only, or local bot operation commands may be executed without asking for another user confirmation. This includes compile, unittest, dry-run, help, detector/status checks, and local CLI validation.

Do not treat every `python3` command as risk-free. High-risk Python commands that write broad state, send external requests, mutate repositories, or apply patches still require careful review. Auto-approval is intended for bounded validation commands such as:

- `python3 -m py_compile ...`
- `python3 -m compileall ...`
- `python3 -m unittest ...`
- `python3 ... --help`
- `python3 ... --dry-run`
- other clearly read-only validation commands

Any Python command that writes files, changes state, sends network requests, applies patches, or mutates repositories must be reviewed as a state-changing command.

## Environment

- Runtime configuration is read from `.env`.
- `.env.example` is a template contract only; never read it as live config.
- Do not commit `.env` or any real token/secret.
- `GITHUB_TOKEN` is optional for the normal local Repo A + SSH workflow.

## Logging

- Runtime logs go to `/Users/liuteli/infra/logs/knowledge-bot`.
- Production logs are daily JSON files named `YYYY-MM-DD.log` in Asia/Singapore time.
- Logs must not include tokens, raw prompts, raw chat history bodies, or other secrets.

## Git

- Work on branch `main`.
- Keep `.env`, local state, logs, caches, and review outputs out of git.
- Push only Repo B changes to the `atlas_kb_bot` GitHub repository.

## Docker Validation

Minimum production startup validation:

```bash
docker-compose config
docker-compose up -d --build
docker-compose ps
docker-compose logs --tail=200
```

If the Docker Compose v2 plugin is available, `docker compose ...` is also acceptable. This host currently supports legacy `docker-compose`.
