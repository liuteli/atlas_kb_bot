#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="rebuild"

case "${1:-}" in
  ""|"--rebuild")
    MODE="rebuild"
    ;;
  "--restart-only")
    MODE="restart-only"
    ;;
  "-h"|"--help")
    cat <<'EOF'
Usage:
  ./scripts/restart.sh --rebuild
  ./scripts/restart.sh --restart-only

Default:
  --rebuild
EOF
    exit 0
    ;;
  *)
    echo "[ERROR] Unknown argument: ${1}" >&2
    exit 1
    ;;
esac

cd "${REPO_DIR}"

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "[ERROR] docker compose or docker-compose is required" >&2
  exit 1
fi

echo "knowledge-bot restart helper"
echo "repo_path: ${REPO_DIR}"
echo "compose_cmd: ${COMPOSE[*]}"
echo "mode: ${MODE}"
echo "warning: app/... code changes are baked into the image and require rebuild/recreate."
echo "warning: plain restart is only for env/config/runtime-only changes."
echo

if [[ "${MODE}" == "rebuild" ]]; then
  echo "== docker compose up -d --build --force-recreate knowledge-bot =="
  "${COMPOSE[@]}" up -d --build --force-recreate knowledge-bot
else
  echo "== docker compose restart knowledge-bot =="
  "${COMPOSE[@]}" restart knowledge-bot
fi
echo

echo "== docker compose ps =="
"${COMPOSE[@]}" ps
echo

echo "== docker compose logs --tail=80 knowledge-bot =="
"${COMPOSE[@]}" logs --tail=80 knowledge-bot
