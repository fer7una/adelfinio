#!/usr/bin/env python3
"""Assemble V2 scene videos into a final episode MP4 and SRT."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from video_pipeline_v2 import assemble_episode_video
except ModuleNotFoundError:
    from scripts.video_pipeline_v2 import assemble_episode_video


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", required=True, help="Path to episode JSON")
    parser.add_argument("--output-video", default=None, help="Optional final video path")
    parser.add_argument("--output-srt", default=None, help="Optional subtitle path")
    args = parser.parse_args()

    video_path, srt_path = assemble_episode_video(
        Path(args.episode),
        output_video=Path(args.output_video) if args.output_video else None,
        output_srt=Path(args.output_srt) if args.output_srt else None,
    )
    print(f"Final video: {video_path}")
    print(f"Subtitles: {srt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
