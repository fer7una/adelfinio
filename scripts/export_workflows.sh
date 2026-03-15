#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="$ROOT_DIR/workflows/exports"
mkdir -p "$OUTPUT_DIR"

: "${N8N_API_URL:=http://localhost:5678}"
: "${N8N_API_KEY:=TODO_N8N_API_KEY}"

if [[ "${1:-}" != "--execute" ]]; then
  echo "Dry-run mode. Use --execute to call n8n API."
  echo "TODO: set real N8N_API_KEY in environment before execution."
  echo "Expected endpoint: $N8N_API_URL/rest/workflows"
  exit 0
fi

curl -sS -X GET "$N8N_API_URL/rest/workflows" \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  -H "Content-Type: application/json" \
  > "$OUTPUT_DIR/workflows_$(date +%Y%m%d_%H%M%S).json"

echo "Export completed in $OUTPUT_DIR"
