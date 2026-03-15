#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TARGET_DATE="${1:-$(date +%F)}"
PLAN_FILE="data/daily_plan/plan-${TARGET_DATE//-/}.json"
EPISODE_BUCKET="data/episodes/generated/plan-${TARGET_DATE//-/}"
RESET_FLAG="${2:-}"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ERROR: ffmpeg is required for rendering. Install it first." >&2
  exit 1
fi

echo "[1/5] Generate daily plan for $TARGET_DATE"
if [[ "$RESET_FLAG" == "--reset-progression" ]]; then
  python3 scripts/generate_daily_plan.py --date "$TARGET_DATE" --output "$PLAN_FILE" --overwrite --reset-progression
else
  python3 scripts/generate_daily_plan.py --date "$TARGET_DATE" --output "$PLAN_FILE" --overwrite
fi

echo "[2/5] Build character bible and emotional timelines"
python3 scripts/build_character_bible.py --events data/timeline/source_events.json --overwrite
python3 scripts/validate_generated_characters.py

echo "[3/5] Generate episode JSON files"
python3 scripts/generate_episodes_from_plan.py --plan "$PLAN_FILE"

echo "[4/5] Render one main and one teaser (quick validation)"
MAIN_EPISODE="$(ls "$EPISODE_BUCKET"/main-*.json | head -n 1)"
TEASER_EPISODE="$(ls "$EPISODE_BUCKET"/teaser-*.json | head -n 1)"
python3 scripts/render_episode_video.py --episode "$MAIN_EPISODE"
python3 scripts/render_episode_video.py --episode "$TEASER_EPISODE"

echo "[5/5] Done"
echo "Episodes: $EPISODE_BUCKET"
echo "Videos: artifacts/videos"
echo "Subtitles: artifacts/subtitles"
