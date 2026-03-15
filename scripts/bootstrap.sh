#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

required_commands=(docker python3 git)
for cmd in "${required_commands[@]}"; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: required command '$cmd' is not installed." >&2
    exit 1
  fi
done

if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: docker compose v2 is required." >&2
  exit 1
fi

if [[ ! -f ".env" ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
  echo "TODO: fill all REPLACE_WITH_* and TODO_* values in .env"
fi

echo "Validating JSON examples..."
python3 scripts/validate_json.py

echo "Starting infrastructure..."
docker compose up -d

echo "Bootstrap completed."
echo "n8n: http://localhost:${N8N_PORT:-5678}"
echo "MinIO API: http://localhost:${MINIO_PORT:-9000}"
echo "MinIO Console: http://localhost:${MINIO_CONSOLE_PORT:-9001}"
