#!/usr/bin/env python3
"""Build V2 per-scene audio plans from events, utterances and alignment."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from video_pipeline_v2 import DEFAULT_RENDER_PLAN_DIR, build_scene_audio_plan_payload, dump_json, find_manifest_for_episode
except ModuleNotFoundError:
    from scripts.video_pipeline_v2 import DEFAULT_RENDER_PLAN_DIR, build_scene_audio_plan_payload, dump_json, find_manifest_for_episode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", required=True, help="Path to episode JSON")
    parser.add_argument("--render-plan-dir", default=str(DEFAULT_RENDER_PLAN_DIR), help="Render plan root")
    parser.add_argument("--assets-dir", default=None, help="Scene assets root directory")
    args = parser.parse_args()

    episode_path = Path(args.episode)
    manifest_path = find_manifest_for_episode(episode_path, Path(args.assets_dir) if args.assets_dir else None) if args.assets_dir else find_manifest_for_episode(episode_path)
    episode_id = __import__("json").loads(episode_path.read_text(encoding="utf-8"))["episode_id"]
    render_dir = Path(args.render_plan_dir) / episode_id
    for events_path in sorted(render_dir.glob("scene_*.events.json")):
        utterances_path = render_dir / events_path.name.replace(".events.json", ".utterances.json")
        alignment_path = render_dir / events_path.name.replace(".events.json", ".alignment.json")
        payload = build_scene_audio_plan_payload(events_path, utterances_path, alignment_path, manifest_path)
        output_path = render_dir / events_path.name.replace(".events.json", ".audio_plan.json")
        dump_json(output_path, payload)
        print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
