#!/usr/bin/env python3
"""Build V2 render events per scene from an episode and its scene asset manifest."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from video_pipeline_v2 import build_scene_events_payload, dump_json, find_manifest_for_episode, scene_paths
except ModuleNotFoundError:
    from scripts.video_pipeline_v2 import build_scene_events_payload, dump_json, find_manifest_for_episode, scene_paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", required=True, help="Path to episode JSON")
    parser.add_argument("--assets-dir", default=None, help="Scene assets root directory")
    args = parser.parse_args()

    episode_path = Path(args.episode)
    manifest_path = find_manifest_for_episode(episode_path, Path(args.assets_dir) if args.assets_dir else None) if args.assets_dir else find_manifest_for_episode(episode_path)
    payloads = build_scene_events_payload(episode_path, manifest_path)
    for payload in payloads:
        path = scene_paths(str(payload["episode_id"]), int(payload["scene_index"])).events
        dump_json(path, payload)
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
