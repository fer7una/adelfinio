#!/usr/bin/env python3
"""Generate main and teaser episode JSON files from a daily plan."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

from story_engine import (
    actor_display_name,
    build_scene_visual_prompt,
    build_teaser_storytelling,
    derive_plot_twist,
    explicit_story_actor_ids,
    trim_text,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMELINE = ROOT / "data" / "timeline" / "source_events.json"
DEFAULT_EPISODES_DIR = ROOT / "data" / "episodes" / "generated"
DEFAULT_CHAR_DIR = ROOT / "data" / "characters"
DEFAULT_CHAR_TIMELINES = ROOT / "data" / "characters" / "timelines"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def event_map(timeline_file: Path) -> dict[str, dict]:
    if not timeline_file.exists():
        raise RuntimeError(
            f"Missing timeline file: {timeline_file}. "
            "Strict mode is enabled: no source -> no episodes."
        )
    payload = load_json(timeline_file)
    if not isinstance(payload, list):
        raise RuntimeError(f"Timeline file must be a JSON array: {timeline_file}")
    out: dict[str, dict] = {}
    for item in payload:
        if isinstance(item, dict) and "event_id" in item:
            out[item["event_id"]] = item
    if not out:
        raise RuntimeError(
            "Timeline has no valid source events. "
            "Strict mode is enabled: no source -> no episodes."
        )
    return out


def load_character_file(character_id: str, char_dir: Path) -> dict:
    path = char_dir / f"{character_id}.json"
    if not path.exists():
        raise RuntimeError(
            f"Missing character file for '{character_id}': {path}. "
            "Run scripts/build_character_bible.py first."
        )
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid character file format: {path}")
    return payload


def load_character_timeline(character_id: str, timelines_dir: Path) -> dict:
    path = timelines_dir / f"{character_id}.json"
    if not path.exists():
        raise RuntimeError(
            f"Missing character timeline for '{character_id}': {path}. "
            "Run scripts/build_character_bible.py first."
        )
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid character timeline format: {path}")
    return payload


def beat_for_actor_event(character_id: str, event_id: str, timelines_dir: Path) -> dict:
    timeline_payload = load_character_timeline(character_id, timelines_dir)
    records = timeline_payload.get("records", [])
    for rec in records:
        if rec.get("event_id") == event_id:
            return {
                "character_id": character_id,
                "emotion_before": rec["emotion_before"],
                "emotion_after": rec["emotion_after"],
                "arc_stage_before": rec["arc_stage_before"],
                "arc_stage_after": rec["arc_stage_after"],
                "beat_summary": trim_text(str(rec["change_summary"]), 300),
            }
    raise RuntimeError(
        f"No timeline record for character '{character_id}' in event '{event_id}'. "
        "Character arc continuity requires per-event records."
    )


def scene_from_outline(scene_outline: dict, location: str, actor_names: list[str]) -> dict:
    dialogue = []
    for row in scene_outline.get("dialogue") or []:
        if not isinstance(row, dict):
            continue
        speaker = trim_text(str(row.get("speaker", "")).strip(), 80)
        line = trim_text(str(row.get("line", "")).strip(), 140)
        if not speaker or not line:
            continue
        delivery = str(row.get("delivery", "normal")).strip().lower()
        dialogue.append(
            {
                "speaker": speaker,
                "line": line,
                "delivery": "shout" if delivery == "shout" else "normal",
            }
        )

    scene_payload = {
        "scene_index": int(scene_outline["scene_index"]),
        "story_role": str(scene_outline.get("story_role", "setup")),
        "source_beat": trim_text(str(scene_outline.get("source_beat", "")), 180),
        "visual_focus": trim_text(str(scene_outline.get("visual_focus", "")), 220),
        "transition_note": trim_text(str(scene_outline.get("transition_note", "")), 180),
        "timing": dict(scene_outline.get("timing") or {}),
        "narration": trim_text(str(scene_outline.get("narration", "")), 220),
        "visual_prompt": build_scene_visual_prompt(scene_outline, location, actor_names),
        "estimated_seconds": int(scene_outline.get("target_duration_seconds", scene_outline.get("estimated_seconds", 6))),
    }
    if dialogue:
        scene_payload["dialogue"] = dialogue
    return scene_payload


def create_main_episode(main_row: dict, events: dict[str, dict], char_dir: Path, timelines_dir: Path) -> dict:
    source_event_ids = main_row["source_event_ids"]
    lead_id = source_event_ids[0]
    if lead_id not in events:
        raise RuntimeError(f"Source event '{lead_id}' not found in timeline.")

    lead_event = events[lead_id]
    event_title = str(lead_event.get("title", f"Evento {lead_id}")).strip()
    event_summary = str(lead_event.get("summary", "")).strip()
    location = str(lead_event.get("location", "Asturias")).strip() or "Asturias"
    actors = lead_event.get("actors") or []
    if not actors:
        raise RuntimeError(f"Source event '{lead_id}' has no actors. Character continuity is required.")
    actors = explicit_story_actor_ids(list(actors), event_summary or event_title)

    for actor in actors:
        load_character_file(actor, char_dir)

    actor_names = [actor_display_name(actor) for actor in actors]
    character_beats = [beat_for_actor_event(actor, lead_id, timelines_dir) for actor in actors]

    storytelling = main_row.get("storytelling")
    if not isinstance(storytelling, dict):
        raise RuntimeError(f"Main episode '{main_row['episode_id']}' has no storytelling block in plan.")
    scene_outline = storytelling.get("scene_outline")
    if not isinstance(scene_outline, list) or not scene_outline:
        raise RuntimeError(f"Main episode '{main_row['episode_id']}' has no scene outline in plan.")

    scenes = [scene_from_outline(scene, location, actor_names) for scene in scene_outline]
    duration = sum(int(scene["estimated_seconds"]) for scene in scenes)
    plot_twist = derive_plot_twist(event_summary or event_title, [scene["narration"] for scene in scenes])
    hook = trim_text(f"{scenes[0]['narration']} {scenes[min(1, len(scenes) - 1)]['narration']}", 300)
    clean_title = re.sub(r"^(as[ií]),?\s+en\s+fin,?\s*", "", trim_text(event_title, 88), flags=re.IGNORECASE).strip()
    if clean_title:
        clean_title = clean_title[0].upper() + clean_title[1:]

    return {
        "episode_id": main_row["episode_id"],
        "episode_type": "main",
        "status": "generated",
        "language": "es",
        "narrative_tone": "epico_historico_oscuro",
        "timeline_event_ids": source_event_ids,
        "parent_episode_id": None,
        "title": trim_text(clean_title, 120),
        "hook": hook,
        "storytelling": {
            "narrative_mode": storytelling.get("narrative_mode", "chronicle"),
            "premise": trim_text(str(storytelling.get("premise", "")), 220),
            "dramatic_question": trim_text(str(storytelling.get("dramatic_question", "")), 220),
            "ending_payoff": trim_text(str(storytelling.get("ending_payoff", "")), 220),
        },
        "plot_twist": plot_twist,
        "scenes": scenes,
        "characters": actors,
        "character_beats": character_beats,
        "duration_seconds": duration,
        "human_review": {
            "required": True,
            "status": "pending",
            "reviewer": None,
            "notes": "Pendiente revision historica y narrativa antes de cualquier publicacion.",
        },
        "asset_refs": {
            "audio": f"local://audio/{main_row['episode_id']}.wav",
            "video": f"local://video/{main_row['episode_id']}.mp4",
            "subtitles": f"local://subtitles/{main_row['episode_id']}.srt",
        },
        "created_at": now_iso(),
    }


def create_teaser_episode(teaser_row: dict, parent_main: dict) -> dict:
    storytelling = teaser_row.get("storytelling")
    if not isinstance(storytelling, dict):
        storytelling = build_teaser_storytelling(parent_main, str(teaser_row.get("strategy", "cliffhanger")))

    scene_outline = storytelling.get("scene_outline")
    if not isinstance(scene_outline, list) or not scene_outline:
        raise RuntimeError(f"Teaser episode '{teaser_row['episode_id']}' has no scene outline in plan.")

    location = ""
    parent_scenes = parent_main.get("scenes") or []
    if parent_scenes:
        location = str(parent_scenes[0].get("visual_focus", ""))
    actor_names = [actor_display_name(actor_id) for actor_id in parent_main.get("characters", [])]
    scenes = [scene_from_outline(scene, location or "Asturias", actor_names) for scene in scene_outline]
    duration = sum(int(scene["estimated_seconds"]) for scene in scenes)

    return {
        "episode_id": teaser_row["episode_id"],
        "episode_type": "teaser",
        "status": "generated",
        "language": "es",
        "narrative_tone": "epico_historico_oscuro",
        "timeline_event_ids": parent_main["timeline_event_ids"][:1],
        "parent_episode_id": teaser_row["parent_episode_id"],
        "title": trim_text(f"Teaser: {parent_main['title']}", 120),
        "hook": trim_text(parent_main["hook"], 300),
        "storytelling": {
            "narrative_mode": storytelling.get("narrative_mode", "teaser"),
            "premise": trim_text(str(storytelling.get("premise", "")), 220),
            "dramatic_question": trim_text(str(storytelling.get("dramatic_question", "")), 220),
            "ending_payoff": trim_text(str(storytelling.get("ending_payoff", "")), 220),
        },
        "scenes": scenes,
        "characters": parent_main["characters"][:2],
        "duration_seconds": duration,
        "human_review": {
            "required": True,
            "status": "pending",
            "reviewer": None,
            "notes": "Validar gancho, CTA y coherencia cronologica.",
        },
        "asset_refs": {
            "audio": f"local://audio/{teaser_row['episode_id']}.wav",
            "video": f"local://video/{teaser_row['episode_id']}.mp4",
            "subtitles": f"local://subtitles/{teaser_row['episode_id']}.srt",
        },
        "created_at": now_iso(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, help="Path to daily plan JSON")
    parser.add_argument("--timeline", default=str(DEFAULT_TIMELINE), help="Path to source events JSON")
    parser.add_argument("--output-dir", default=str(DEFAULT_EPISODES_DIR), help="Directory for generated episodes")
    parser.add_argument("--characters-dir", default=str(DEFAULT_CHAR_DIR), help="Directory of character JSON files")
    parser.add_argument(
        "--character-timelines-dir",
        default=str(DEFAULT_CHAR_TIMELINES),
        help="Directory of character timeline JSON files",
    )
    args = parser.parse_args()

    try:
        plan_path = Path(args.plan)
        timeline_path = Path(args.timeline)
        output_dir = Path(args.output_dir)
        char_dir = Path(args.characters_dir)
        timelines_dir = Path(args.character_timelines_dir)

        plan = load_json(plan_path)
        events = event_map(timeline_path)

        for main_row in plan["main_episodes"]:
            for event_id in main_row["source_event_ids"]:
                if event_id not in events:
                    raise RuntimeError(
                        f"Plan references event '{event_id}' that does not exist in timeline '{timeline_path}'."
                    )

        date_compact = plan["date"].replace("-", "")
        plan_bucket = output_dir / f"plan-{date_compact}"

        main_map: dict[str, dict] = {}
        written_files: list[Path] = []

        for main_row in plan["main_episodes"]:
            episode = create_main_episode(main_row, events, char_dir, timelines_dir)
            episode_path = plan_bucket / f"{episode['episode_id']}.json"
            dump_json(episode_path, episode)
            main_map[episode["episode_id"]] = episode
            written_files.append(episode_path)

        for teaser_row in plan["teaser_episodes"]:
            parent_id = teaser_row["parent_episode_id"]
            parent = main_map.get(parent_id)
            if not parent:
                raise RuntimeError(f"Parent main episode missing for teaser: {parent_id}")
            teaser = create_teaser_episode(teaser_row, parent)
            teaser_path = plan_bucket / f"{teaser['episode_id']}.json"
            dump_json(teaser_path, teaser)
            written_files.append(teaser_path)

        print(f"Generated {len(written_files)} episode files in {plan_bucket}")
        for path in written_files:
            print(path)
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
