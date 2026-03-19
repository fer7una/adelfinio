#!/usr/bin/env python3
"""Render V2 clean scene videos from image + final scene audio."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from video_pipeline_v2 import DEFAULT_RENDER_PLAN_DIR, find_manifest_for_episode, render_clean_scene_video, scene_paths
except ModuleNotFoundError:
    from scripts.video_pipeline_v2 import DEFAULT_RENDER_PLAN_DIR, find_manifest_for_episode, render_clean_scene_video, scene_paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", required=True, help="Path to episode JSON")
    parser.add_argument("--render-plan-dir", default=str(DEFAULT_RENDER_PLAN_DIR), help="Render plan root")
    parser.add_argument("--assets-dir", default=None, help="Scene assets root directory")
    parser.add_argument("--width", type=int, default=1080, help="Output width")
    parser.add_argument("--height", type=int, default=1920, help="Output height")
    parser.add_argument("--fps", type=int, default=30, help="Output fps")
    args = parser.parse_args()

    episode_path = Path(args.episode)
    episode_id = __import__("json").loads(episode_path.read_text(encoding="utf-8"))["episode_id"]
    manifest_path = find_manifest_for_episode(episode_path, Path(args.assets_dir) if args.assets_dir else None) if args.assets_dir else find_manifest_for_episode(episode_path)
    render_dir = Path(args.render_plan_dir) / episode_id
    for events_path in sorted(render_dir.glob("scene_*.events.json")):
        scene_index = int(events_path.stem.split(".")[0].split("_")[1])
        audio_plan_path = render_dir / events_path.name.replace(".events.json", ".audio_plan.json")
        output_path = scene_paths(episode_id, scene_index).clean_video
        render_clean_scene_video(events_path, audio_plan_path, manifest_path, output_path, width=args.width, height=args.height, fps=args.fps)
        print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
