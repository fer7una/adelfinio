#!/usr/bin/env python3
"""Print a compact storyboard view for an episode JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid JSON payload: {path}")
    return payload


def normalize(text: str) -> str:
    return " ".join((text or "").split()).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", required=True, help="Path to episode JSON")
    args = parser.parse_args()

    episode_path = Path(args.episode)
    episode = load_json(episode_path)
    scenes = episode.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise RuntimeError("Episode has no scenes.")

    print(f"Episode: {episode.get('episode_id')}")
    print(f"Type: {episode.get('episode_type')} | Scenes: {len(scenes)} | Duration: {episode.get('duration_seconds')}s")
    print("")
    for scene in scenes:
        idx = int(scene.get("scene_index", 0))
        narration = normalize(str(scene.get("narration", "")))
        visual_prompt = normalize(str(scene.get("visual_prompt", "")))
        print(f"[Scene {idx:02d}]")
        print(f"Texto: {narration}")
        print(f"Prompt: {visual_prompt}")
        dialogue = scene.get("dialogue")
        if isinstance(dialogue, list) and dialogue:
            for row in dialogue:
                if not isinstance(row, dict):
                    continue
                speaker = normalize(str(row.get("speaker", "")))
                line = normalize(str(row.get("line", "")))
                if speaker and line:
                    print(f"Dialogo: {speaker} -> {line}")
        print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
