#!/usr/bin/env python3
"""Create V2 word-level alignment files for scene utterances."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from video_pipeline_v2 import DEFAULT_RENDER_PLAN_DIR, align_scene_audio_payload, dump_json
except ModuleNotFoundError:
    from scripts.video_pipeline_v2 import DEFAULT_RENDER_PLAN_DIR, align_scene_audio_payload, dump_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-id", required=True, help="Episode id")
    parser.add_argument("--render-plan-dir", default=str(DEFAULT_RENDER_PLAN_DIR), help="Render plan root")
    parser.add_argument("--mock", action="store_true", help="Estimate alignment instead of calling OpenAI transcription")
    parser.add_argument("--transcription-model", default=None, help="Optional transcription model override")
    parser.add_argument("--allow-estimated-fallback", action="store_true", help="Allow estimated alignment fallback if transcription fails")
    args = parser.parse_args()

    render_dir = Path(args.render_plan_dir) / args.episode_id
    for utterances_path in sorted(render_dir.glob("scene_*.utterances.json")):
        clips_path = render_dir / utterances_path.name.replace(".utterances.json", ".audio_clips.json")
        if not clips_path.exists():
            raise RuntimeError(f"Missing synthesized audio payload: {clips_path}")
        payload = align_scene_audio_payload(
            utterances_path,
            __import__("json").loads(clips_path.read_text(encoding="utf-8")),
            mock=args.mock,
            transcription_model=args.transcription_model,
            allow_estimated_fallback=args.allow_estimated_fallback,
        )
        output_path = render_dir / utterances_path.name.replace(".utterances.json", ".alignment.json")
        dump_json(output_path, payload)
        print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
