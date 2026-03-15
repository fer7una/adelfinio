#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SOURCE="${1:-docs/chronicles/01-La gran aventura del reino de Asturias.pdf}"
TARGET_DATE="${2:-$(date +%F)}"
RESET_FLAG="${3:-}"

PLAN_FILE="data/daily_plan/plan-${TARGET_DATE//-/}.json"
EPISODE_BUCKET="data/episodes/generated/plan-${TARGET_DATE//-/}"
SIDECAR_DIR="docs/chronicles/sidecar_text"

mkdir -p "$SIDECAR_DIR"

echo "[1/7] Extract source events from chronicles"
if ! python3 scripts/extract_source_events.py --sources "$SOURCE" --pdf-sidecar-dir "$SIDECAR_DIR" --overwrite; then
  if [[ "$SOURCE" == *.pdf ]] && command -v tesseract >/dev/null 2>&1; then
    sidecar="$SIDECAR_DIR/$(basename "${SOURCE%.pdf}").txt"
    echo "PDF appears scanned. Running OCR sidecar generation..."
    bash scripts/ocr_pdf_to_sidecar.sh "$SOURCE" "$sidecar" 1 120
    python3 scripts/extract_source_events.py --sources "$SOURCE" --pdf-sidecar-dir "$SIDECAR_DIR" --overwrite
  else
    echo "ERROR: Source extraction failed. Provide OCR text sidecar or install tesseract." >&2
    exit 1
  fi
fi

echo "[2/7] Build character bible and emotional arcs"
python3 scripts/build_character_bible.py --events data/timeline/source_events.json --overwrite
python3 scripts/validate_generated_characters.py

echo "[3/7] Generate daily plan"
if [[ "$RESET_FLAG" == "--reset-progression" ]]; then
  python3 scripts/generate_daily_plan.py --date "$TARGET_DATE" --output "$PLAN_FILE" --overwrite --reset-progression
else
  python3 scripts/generate_daily_plan.py --date "$TARGET_DATE" --output "$PLAN_FILE" --overwrite
fi

echo "[4/7] Generate episodes from plan"
python3 scripts/generate_episodes_from_plan.py --plan "$PLAN_FILE"
python3 scripts/validate_generated_episodes.py --dir "$EPISODE_BUCKET"

echo "[5/7] Render visual videos (if ffmpeg exists)"
if command -v ffmpeg >/dev/null 2>&1; then
  for ep in "$EPISODE_BUCKET"/*.json; do
    python3 scripts/render_episode_video.py --episode "$ep"
  done
else
  echo "WARNING: ffmpeg not found. Install ffmpeg to render MP4 outputs."
fi

echo "[6/7] Summary"
echo "Events: data/timeline/source_events.json"
echo "Characters: data/characters/*.json"
echo "Character timelines: data/characters/timelines/*.json"
echo "Episodes: $EPISODE_BUCKET"
echo "Videos: artifacts/videos (if ffmpeg installed)"

echo "[7/7] Done"
