#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SOURCE="${1:-docs/chronicles/01-La gran aventura del reino de Asturias.pdf}"
STORY_ID="${2:-}"

exec bash scripts/run_story_pipeline_from_source.sh "$SOURCE" ${STORY_ID:+"$STORY_ID"}
