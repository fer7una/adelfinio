#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || $# -lt 1 ]]; then
  echo "Usage:"
  echo "  bash scripts/run_story_pipeline_from_source.sh <source_file> [story_id] [--approve-source <reviewer>] [--mock] [--fallback-mock-on-billing-error] [--overwrite]"
  echo
  echo "Examples:"
  echo "  bash scripts/run_story_pipeline_from_source.sh docs/chronicles/01-La\\ gran\\ aventura\\ del\\ reino\\ de\\ Asturias.pdf"
  echo "  bash scripts/run_story_pipeline_from_source.sh docs/chronicles/01-La\\ gran\\ aventura\\ del\\ reino\\ de\\ Asturias.pdf story-resistencia-pelayo --approve-source editor_historia --mock"
  exit 0
fi

SOURCE="$1"
shift || true

STORY_ID=""
APPROVE_SOURCE=0
REVIEWER="human_reviewer"
RENDER_MODE=""
OVERWRITE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --approve-source)
      APPROVE_SOURCE=1
      REVIEWER="${2:-human_reviewer}"
      shift 2
      ;;
    --mock|--fallback-mock-on-billing-error)
      RENDER_MODE="$1"
      shift
      ;;
    --overwrite)
      OVERWRITE=1
      shift
      ;;
    *)
      if [[ -z "$STORY_ID" ]]; then
        STORY_ID="$1"
        shift
      else
        echo "ERROR: Unknown argument: $1" >&2
        exit 1
      fi
      ;;
  esac
done

if [[ ! -f "$SOURCE" ]]; then
  echo "ERROR: source file not found: $SOURCE" >&2
  exit 1
fi

SOURCE_STEM="$(basename "${SOURCE%.*}")"
SOURCE_SLUG="$(printf '%s' "$SOURCE_STEM" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/-\+/-/g' | sed 's/^-//; s/-$//')"
SOURCE_PACK="data/source/${SOURCE_SLUG}.source_pack.json"
CHARACTER_BIBLE="data/characters/${SOURCE_SLUG}.character_bible.json"
STORY_CATALOG="data/story/${SOURCE_SLUG}.story_catalog.json"
EPISODE_DIR="data/episodes/generated/${SOURCE_SLUG}"
DERIVED_EVENTS="data/timeline/source_events.json"

OVERWRITE_FLAG=""
if [[ "$OVERWRITE" -eq 1 ]]; then
  OVERWRITE_FLAG="--overwrite"
fi

echo "[1/7] Build source pack from source"
python3 scripts/build_source_pack.py \
  --source "$SOURCE" \
  --output "$SOURCE_PACK" \
  --derived-events-output "$DERIVED_EVENTS" \
  $OVERWRITE_FLAG

if [[ "$APPROVE_SOURCE" -eq 1 ]]; then
  echo "[2/7] Approve source pack"
  python3 scripts/review_source_pack.py --source-pack "$SOURCE_PACK" --approve --reviewer "$REVIEWER" --notes "Aprobado via wrapper automatizado."
else
  echo "[2/7] Source pack review summary"
  python3 scripts/review_source_pack.py --source-pack "$SOURCE_PACK" --summary
fi

SOURCE_STATUS="$(python3 - <<'PY' "$SOURCE_PACK"
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
print((payload.get("review") or {}).get("status", "pending"))
PY
)"

if [[ "$SOURCE_STATUS" != "approved" ]]; then
  echo "Source pack is not approved yet. Review and approve it before generating characters and stories." >&2
  echo "Command: python3 scripts/review_source_pack.py --source-pack \"$SOURCE_PACK\" --approve --reviewer <nombre>" >&2
  exit 2
fi

echo "[3/7] Generate character bible and compatibility character files"
python3 scripts/generate_character_bible.py \
  --source-pack "$SOURCE_PACK" \
  --output "$CHARACTER_BIBLE" \
  --characters-dir data/characters \
  --timelines-dir data/characters/timelines \
  $OVERWRITE_FLAG

echo "[4/7] Generate story catalog"
python3 scripts/generate_story_catalog.py \
  --source-pack "$SOURCE_PACK" \
  --character-bible "$CHARACTER_BIBLE" \
  --output "$STORY_CATALOG" \
  $OVERWRITE_FLAG

if [[ -z "$STORY_ID" ]]; then
  echo "[5/7] Story selection required"
  python3 - <<'PY' "$STORY_CATALOG"
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
print("Available stories:")
for story in payload.get("stories", []):
    print(f"- {story['story_id']}: {story['title']}")
PY
  echo "Select one story_id and rerun the wrapper with that value as the second argument." >&2
  exit 3
fi

echo "[5/7] Generate final dramatic episode"
python3 scripts/generate_episode_from_story.py \
  --source-pack "$SOURCE_PACK" \
  --character-bible "$CHARACTER_BIBLE" \
  --story-catalog "$STORY_CATALOG" \
  --story-id "$STORY_ID" \
  --output-dir "$EPISODE_DIR" \
  $OVERWRITE_FLAG

EPISODE_PATH="$(python3 - <<'PY' "$EPISODE_DIR" "$STORY_ID"
import json, pathlib, sys
episode_dir = pathlib.Path(sys.argv[1])
story_id = sys.argv[2].replace("story-", "")
matches = sorted(episode_dir.glob(f"*{story_id[:48]}*.json"))
if matches:
    print(matches[0])
    raise SystemExit(0)
all_files = sorted(episode_dir.glob("*.json"))
if not all_files:
    raise SystemExit(1)
print(all_files[-1])
PY
)"

echo "[6/7] Validate pipeline artifacts"
python3 scripts/validate_generated_characters.py
python3 scripts/validate_story_pipeline.py \
  --source-pack "$SOURCE_PACK" \
  --character-bible "$CHARACTER_BIBLE" \
  --story-catalog "$STORY_CATALOG" \
  --episode "$EPISODE_PATH"
python3 scripts/validate_generated_episodes.py --dir "$EPISODE_DIR"

echo "[7/7] Render final video"
if command -v ffmpeg >/dev/null 2>&1; then
  if [[ -n "$RENDER_MODE" ]]; then
    bash scripts/run_final_ai_video_pipeline_v2.sh "$EPISODE_PATH" "$RENDER_MODE"
  else
    bash scripts/run_final_ai_video_pipeline_v2.sh "$EPISODE_PATH"
  fi
else
  echo "WARNING: ffmpeg not found. Episode JSON was generated but rendering was skipped."
fi

echo "Done."
echo "Source pack: $SOURCE_PACK"
echo "Character bible: $CHARACTER_BIBLE"
echo "Story catalog: $STORY_CATALOG"
echo "Episode: $EPISODE_PATH"
