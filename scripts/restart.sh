#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
START_TS="$(date '+%Y-%m-%d %H:%M:%S')"

cd "$REPO_DIR"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "[ERROR] docker compose or docker-compose is required" >&2
  exit 1
fi

LOG_ROOT="${KB_LOG_ROOT:-/Users/liuteli/infra/logs/knowledge-bot}"
TODAY="$(TZ=Asia/Singapore date '+%Y-%m-%d')"
TODAY_LOG="${LOG_ROOT}/${TODAY}.log"

echo "knowledge-bot restart"
echo "start_time: ${START_TS}"
echo "repo_path: ${REPO_DIR}"
echo "compose_cmd: ${COMPOSE[*]}"
echo

echo "== compose down =="
"${COMPOSE[@]}" down
echo "compose_down: ok"
echo

echo "== compose up -d --build =="
"${COMPOSE[@]}" up -d --build
echo "compose_up: ok"
echo

echo "== current container status =="
"${COMPOSE[@]}" ps
echo

echo "logs_root: ${LOG_ROOT}"
echo "today_log: ${TODAY_LOG}"
if [[ -f "${TODAY_LOG}" ]]; then
  echo
  echo "== recent bot log: ${TODAY_LOG} =="
  tail -n 80 "${TODAY_LOG}"
else
  echo "recent bot log not found yet"
fi
