#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOWS_DIR="$ROOT_DIR/workflows"

: "${N8N_API_URL:=http://localhost:5678}"
: "${N8N_API_KEY:=TODO_N8N_API_KEY}"

if [[ "${1:-}" != "--execute" ]]; then
  echo "Dry-run mode. Use --execute to call n8n API."
  echo "TODO: set real N8N_API_KEY in environment before execution."
fi

for file in "$WORKFLOWS_DIR"/*.json; do
  echo "Processing $file"
  if [[ "${1:-}" == "--execute" ]]; then
    curl -sS -X POST "$N8N_API_URL/rest/workflows" \
      -H "X-N8N-API-KEY: $N8N_API_KEY" \
      -H "Content-Type: application/json" \
      --data-binary "@$file"
    echo
  fi
done

if [[ "${1:-}" != "--execute" ]]; then
  echo "No API requests executed."
fi
