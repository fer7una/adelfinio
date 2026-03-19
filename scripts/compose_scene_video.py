#!/usr/bin/env python3
"""Compose V2 scene videos from clean clips, overlay timelines and camera plans."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from video_pipeline_v2 import DEFAULT_OVERLAY_DIR, DEFAULT_RENDER_PLAN_DIR, compose_scene_video, scene_paths
except ModuleNotFoundError:
    from scripts.video_pipeline_v2 import DEFAULT_OVERLAY_DIR, DEFAULT_RENDER_PLAN_DIR, compose_scene_video, scene_paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-id", required=True, help="Episode id")
    parser.add_argument("--render-plan-dir", default=str(DEFAULT_RENDER_PLAN_DIR), help="Render plan root")
    parser.add_argument("--overlay-assets-dir", default=str(DEFAULT_OVERLAY_DIR), help="Directory with narration/dialogue/shout SVG overlays")
    parser.add_argument("--font-file", default=None, help="Optional font override")
    parser.add_argument("--width", type=int, default=1080, help="Output width")
    parser.add_argument("--height", type=int, default=1920, help="Output height")
    parser.add_argument("--fps", type=int, default=30, help="Output fps")
    args = parser.parse_args()

    render_dir = Path(args.render_plan_dir) / args.episode_id
    for overlay_timeline_path in sorted(render_dir.glob("scene_*.overlay_timeline.json")):
        scene_index = int(overlay_timeline_path.stem.split(".")[0].split("_")[1])
        camera_plan_path = render_dir / overlay_timeline_path.name.replace(".overlay_timeline.json", ".camera_plan.json")
        output_path = scene_paths(args.episode_id, scene_index).composited_video
        compose_scene_video(
            overlay_timeline_path,
            camera_plan_path,
            output_path,
            overlay_assets_dir=Path(args.overlay_assets_dir),
            font_file=args.font_file,
            width=args.width,
            height=args.height,
            fps=args.fps,
        )
        print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
