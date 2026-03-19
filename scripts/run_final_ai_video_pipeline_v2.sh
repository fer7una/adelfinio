#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo "Usage:"
  echo "  bash scripts/run_final_ai_video_pipeline_v2.sh <episode_json_or_directory> [--mock]"
  echo ""
  echo "Examples:"
  echo "  bash scripts/run_final_ai_video_pipeline_v2.sh data/episodes/generated/story-catalog/main-20262011-visigoth-collapse-711.json"
  echo "  bash scripts/run_final_ai_video_pipeline_v2.sh data/episodes/generated/story-catalog --mock"
  exit 0
fi

TARGET="${1:-data/episodes/generated/story-catalog}"
MODE="${2:-}"

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
  echo "[1/11] Generate scene assets -> $episode"
  if [[ "$MODE" == "--mock" ]]; then
    python3 scripts/generate_scene_assets.py --episode "$episode" --mock
  else
    python3 scripts/generate_scene_assets.py --episode "$episode"
  fi

  EPISODE_ID="$(python3 - <<PY
import json
from pathlib import Path
print(json.loads(Path("$episode").read_text(encoding="utf-8"))["episode_id"])
PY
)"

  echo "[2/11] Build scene events -> $EPISODE_ID"
  python3 scripts/build_scene_events.py --episode "$episode"

  echo "[3/11] Build utterances -> $EPISODE_ID"
  python3 scripts/build_scene_utterances.py --episode-id "$EPISODE_ID"

  echo "[4/11] Synthesize scene audio -> $EPISODE_ID"
  if [[ "$MODE" == "--mock" ]]; then
    python3 scripts/synthesize_scene_audio.py --episode-id "$EPISODE_ID" --mock
  else
    python3 scripts/synthesize_scene_audio.py --episode-id "$EPISODE_ID"
  fi

  echo "[5/11] Align scene audio -> $EPISODE_ID"
  if [[ "$MODE" == "--mock" ]]; then
    python3 scripts/align_scene_audio.py --episode-id "$EPISODE_ID" --mock
  else
    python3 scripts/align_scene_audio.py --episode-id "$EPISODE_ID" --allow-estimated-fallback
  fi

  echo "[6/11] Build scene audio plan -> $EPISODE_ID"
  python3 scripts/build_scene_audio_plan.py --episode "$episode"

  echo "[7/11] Render clean scenes -> $EPISODE_ID"
  python3 scripts/render_clean_scene_video.py --episode "$episode"

  echo "[8/11] Build overlay timelines -> $EPISODE_ID"
  python3 scripts/build_overlay_timeline.py --episode "$episode"

  echo "[9/11] Build camera plans -> $EPISODE_ID"
  python3 scripts/build_camera_plan.py --episode "$episode"

  echo "[10/11] Compose scene videos -> $EPISODE_ID"
  python3 scripts/compose_scene_video.py --episode-id "$EPISODE_ID"

  echo "[11/11] Assemble final episode -> $EPISODE_ID"
  python3 scripts/assemble_episode_video.py --episode "$episode"
done

echo "Done."
echo "Render plans: artifacts/render_plan"
echo "Clean videos: artifacts/videos/clean"
echo "Composited scenes: artifacts/videos/composited"
echo "Final videos: artifacts/videos/final"
