#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${REPO_DIR}"

echo "knowledge-bot local runner"
echo "mode: local source outside Docker"
echo "production: Telegram polling runs in the Docker service knowledge-bot"
echo "note: app/... code changes require docker compose up -d --build --force-recreate knowledge-bot for production"
echo "note: this script does not update or restart the running container"
echo

python3 -m app.cli bot "$@"
