#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo "Usage:"
  echo "  bash scripts/run_final_ai_video_pipeline.sh <episode_json_or_directory> [--mock] [--fallback-mock-on-billing-error]"
  echo ""
  echo "Examples:"
  echo "  bash scripts/run_final_ai_video_pipeline.sh data/episodes/generated/plan-20260315"
  echo "  bash scripts/run_final_ai_video_pipeline.sh data/episodes/generated/plan-20260315/main-20260315-linea_01.json"
  echo "  bash scripts/run_final_ai_video_pipeline.sh data/episodes/generated/plan-20260315 --mock"
  echo "  bash scripts/run_final_ai_video_pipeline.sh data/episodes/generated/plan-20260315 --fallback-mock-on-billing-error"
  exit 0
fi

TARGET="${1:-data/episodes/generated/plan-$(date +%Y%m%d)}"
MODE="${2:-}"
FALLBACK="${3:-}"

if [[ "$MODE" == "--mock" ]]; then
  echo "Running in MOCK mode (no OpenAI API calls)."
fi
if [[ "$MODE" == "--fallback-mock-on-billing-error" ]]; then
  FALLBACK="$MODE"
  MODE=""
fi

if [[ -d "$TARGET" ]]; then
  shopt -s nullglob
  EPISODES=("$TARGET"/*.json)
  shopt -u nullglob
else
  EPISODES=("$TARGET")
fi

if [[ ${#EPISODES[@]} -eq 0 ]]; then
  echo "ERROR: no episode JSON files found in target: $TARGET" >&2
  exit 1
fi

for episode in "${EPISODES[@]}"; do
  echo "[1/2] Generate scene assets -> $episode"
  if [[ "$MODE" == "--mock" ]]; then
    python3 scripts/generate_scene_assets.py --episode "$episode" --mock
  elif [[ "$FALLBACK" == "--fallback-mock-on-billing-error" ]]; then
    python3 scripts/generate_scene_assets.py --episode "$episode" --fallback-mock-on-billing-error
  else
    python3 scripts/generate_scene_assets.py --episode "$episode"
  fi

  echo "[2/2] Compose final MP4 -> $episode"
  python3 scripts/compose_final_video.py --episode "$episode"
done

echo "Done."
echo "Final videos: artifacts/videos/final"
echo "Final subtitles: artifacts/subtitles/final"
