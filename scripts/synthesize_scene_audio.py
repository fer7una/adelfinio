#!/usr/bin/env python3
"""Synthesize utterance-level audio clips for V2 render scenes."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from video_pipeline_v2 import DEFAULT_RENDER_PLAN_DIR, dump_json, scene_paths, synthesize_scene_audio_payload
except ModuleNotFoundError:
    from scripts.video_pipeline_v2 import DEFAULT_RENDER_PLAN_DIR, dump_json, scene_paths, synthesize_scene_audio_payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-id", required=True, help="Episode id")
    parser.add_argument("--render-plan-dir", default=str(DEFAULT_RENDER_PLAN_DIR), help="Render plan root")
    parser.add_argument("--mock", action="store_true", help="Generate mock silence clips instead of calling OpenAI")
    parser.add_argument("--tts-model", default=None, help="Optional TTS model override")
    args = parser.parse_args()

    render_dir = Path(args.render_plan_dir) / args.episode_id
    for utterances_path in sorted(render_dir.glob("scene_*.utterances.json")):
        scene_index = int(utterances_path.stem.split(".")[0].split("_")[1])
        paths = scene_paths(args.episode_id, scene_index)
        payload = synthesize_scene_audio_payload(
            utterances_path,
            paths.audio_dir,
            mock=args.mock,
            tts_model=args.tts_model,
        )
        output_path = render_dir / utterances_path.name.replace(".utterances.json", ".audio_clips.json")
        dump_json(output_path, payload)
        print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
