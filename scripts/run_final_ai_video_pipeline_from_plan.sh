#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ $# -lt 1 ]]; then
  echo "Usage:"
  echo "  bash scripts/run_final_ai_video_pipeline_from_plan.sh <plan_json> [--mock] [--fallback-mock-on-billing-error]"
  echo
  echo "Examples:"
  echo "  bash scripts/run_final_ai_video_pipeline_from_plan.sh data/daily_plan/plan-20260315.json"
  echo "  bash scripts/run_final_ai_video_pipeline_from_plan.sh data/daily_plan/plan-20260315.json --mock"
  exit 1
fi

PLAN_PATH="$1"
shift || true

if [[ ! -f "$PLAN_PATH" ]]; then
  echo "ERROR: Plan file not found: $PLAN_PATH" >&2
  exit 1
fi

PLAN_BASENAME="$(basename "$PLAN_PATH")"
PLAN_STEM="${PLAN_BASENAME%.json}"
EPISODE_DIR="data/episodes/generated/${PLAN_STEM}"

python3 scripts/generate_episodes_from_plan.py --plan "$PLAN_PATH"
python3 scripts/validate_generated_episodes.py --dir "$EPISODE_DIR"
bash scripts/run_final_ai_video_pipeline.sh "$EPISODE_DIR" "$@"
