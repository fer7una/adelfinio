#!/usr/bin/env python3
"""Build V2 scene utterances from scene events."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from video_pipeline_v2 import DEFAULT_RENDER_PLAN_DIR, build_scene_utterances_payload, dump_json
except ModuleNotFoundError:
    from scripts.video_pipeline_v2 import DEFAULT_RENDER_PLAN_DIR, build_scene_utterances_payload, dump_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-id", required=True, help="Episode id")
    parser.add_argument("--render-plan-dir", default=str(DEFAULT_RENDER_PLAN_DIR), help="Render plan root")
    args = parser.parse_args()

    render_dir = Path(args.render_plan_dir) / args.episode_id
    for events_path in sorted(render_dir.glob("scene_*.events.json")):
        payload = build_scene_utterances_payload(events_path)
        output_path = render_dir / events_path.name.replace(".events.json", ".utterances.json")
        dump_json(output_path, payload)
        print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
