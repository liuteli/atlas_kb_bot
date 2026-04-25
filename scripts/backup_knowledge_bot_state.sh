#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"
BACKUP_ROOT="/Users/liuteli/infra/backups/manual/knowledge-bot"
DRY_RUN=0

if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

KB_LOG_ROOT="${KB_LOG_ROOT:-/Users/liuteli/infra/logs/knowledge-bot}"
KB_STATE_ROOT="${KB_STATE_ROOT:-/Users/liuteli/infra/knowledge_local/obsidian-main/atlas/99_SYSTEM/jobs/knowledge-bot}"
KB_REVIEW_OUTPUT_ROOT="${KB_REVIEW_OUTPUT_ROOT:-${KB_STATE_ROOT}/reviews}"

STAMP="$(date +%Y%m%d_%H%M%S)"
DEST="${BACKUP_ROOT}/${STAMP}"

echo "knowledge-bot backup destination: ${DEST}"
for target in "${KB_LOG_ROOT}" "${KB_STATE_ROOT}" "${KB_REVIEW_OUTPUT_ROOT}"; do
  if [[ -e "${target}" ]]; then
    echo "include: ${target}"
  else
    echo "missing: ${target}"
  fi
done

if [[ "${DRY_RUN}" == "1" ]]; then
  echo "dry-run: no files written"
  exit 0
fi

mkdir -p "${DEST}"
for target in "${KB_LOG_ROOT}" "${KB_STATE_ROOT}" "${KB_REVIEW_OUTPUT_ROOT}"; do
  if [[ -e "${target}" ]]; then
    name="$(basename "${target}")"
    parent="$(dirname "${target}")"
    tar -czf "${DEST}/${name}.tar.gz" -C "${parent}" "${name}"
  fi
done
echo "backup complete: ${DEST}"
