#!/usr/bin/env python3
"""Validate the new source-first story pipeline artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from schema_validation import build_validator, load_json
except ImportError as exc:
    print("ERROR: missing dependency 'jsonschema'. Install with: python3 -m pip install jsonschema")
    raise SystemExit(1) from exc

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACK_SCHEMA = ROOT / "schemas" / "source_pack.schema.json"
CHARACTER_BIBLE_SCHEMA = ROOT / "schemas" / "character_bible.schema.json"
STORY_CATALOG_SCHEMA = ROOT / "schemas" / "story_catalog.schema.json"
EPISODE_SCHEMA = ROOT / "schemas" / "episode.schema.json"


def schema_errors(schema_path: Path, payload: dict) -> list[str]:
    validator = build_validator(schema_path)
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
    out: list[str] = []
    for err in errors:
        location = ".".join(str(x) for x in err.absolute_path) or "<root>"
        out.append(f"{location}: {err.message}")
    return out


def semantic_errors(source_pack: dict, character_bible: dict, story_catalog: dict, episode: dict | None) -> list[str]:
    errors: list[str] = []

    derived_event_ids = {event["event_id"] for event in source_pack.get("derived_events", []) if isinstance(event, dict)}
    if (source_pack.get("review") or {}).get("status") != "approved":
        errors.append("source_pack.review.status must be approved before downstream generation.")

    last_index = 0
    for story in story_catalog.get("stories", []):
        current_index = int(story.get("chronology_index", 0))
        if current_index < last_index:
            errors.append(f"story chronology is not sorted: {story.get('story_id')}")
        last_index = current_index
        for event_id in story.get("source_event_ids", []):
            if event_id not in derived_event_ids:
                errors.append(f"story {story.get('story_id')} references unknown event_id {event_id}")

    bible_ids = {character["character_id"] for character in character_bible.get("characters", [])}
    for story in story_catalog.get("stories", []):
        for character_id in story.get("characters", []):
            if character_id not in bible_ids:
                errors.append(f"story {story.get('story_id')} references unknown character {character_id}")

    if episode is None:
        return errors

    for event_id in episode.get("timeline_event_ids", []):
        if event_id not in derived_event_ids:
            errors.append(f"episode references unknown timeline_event_id {event_id}")

    episode_characters = set(episode.get("characters", []))
    if not episode_characters:
        errors.append("episode must list at least one character.")

    for scene in episode.get("scenes", []):
        scene_cast = set(scene.get("scene_cast", []))
        if scene_cast and not scene_cast.issubset(episode_characters):
            errors.append(f"scene {scene.get('scene_index')} has scene_cast outside episode.characters")
        if scene.get("subtext_or_private_state_summary") and not scene.get("inference_flags"):
            errors.append(f"scene {scene.get('scene_index')} has subtext but no inference_flags")
        if scene.get("behavioral_notes") and not (scene.get("source_evidence") or scene.get("inference_flags")):
            errors.append(f"scene {scene.get('scene_index')} has behavioral_notes without evidence or inference flags")
        if not scene.get("source_evidence") and not scene.get("inference_flags"):
            errors.append(f"scene {scene.get('scene_index')} needs source_evidence or inference_flags")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-pack", required=True, help="Path to source_pack.json")
    parser.add_argument("--character-bible", required=True, help="Path to character_bible.json")
    parser.add_argument("--story-catalog", required=True, help="Path to story_catalog.json")
    parser.add_argument("--episode", default=None, help="Optional path to a generated episode.json")
    args = parser.parse_args()

    source_pack = load_json(Path(args.source_pack))
    character_bible = load_json(Path(args.character_bible))
    story_catalog = load_json(Path(args.story_catalog))
    episode = load_json(Path(args.episode)) if args.episode else None

    failures: list[str] = []
    for label, schema_path, payload in [
        ("source_pack", SOURCE_PACK_SCHEMA, source_pack),
        ("character_bible", CHARACTER_BIBLE_SCHEMA, character_bible),
        ("story_catalog", STORY_CATALOG_SCHEMA, story_catalog),
    ]:
        for err in schema_errors(schema_path, payload):
            failures.append(f"{label}: {err}")

    if episode is not None:
        for err in schema_errors(EPISODE_SCHEMA, episode):
            failures.append(f"episode: {err}")

    failures.extend(semantic_errors(source_pack, character_bible, story_catalog, episode))

    if failures:
        print("Validation failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Story pipeline artifacts are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
