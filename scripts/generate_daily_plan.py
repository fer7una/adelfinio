#!/usr/bin/env python3
"""Generate a daily plan with 2 main episodes and 2 teaser episodes."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from story_engine import build_main_storytelling, build_teaser_storytelling

ROOT = Path(__file__).resolve().parents[1]
TIMELINE_FILE = ROOT / "data" / "timeline" / "source_events.json"
PROGRESSION_FILE = ROOT / "data" / "timeline" / "progression_state.json"


def read_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def load_timeline_events(timeline_path: Path) -> list[dict]:
    if not timeline_path.exists():
        raise RuntimeError(
            f"Missing source timeline file: {timeline_path}. "
            "Strict mode is enabled: no source -> no plan."
        )

    payload = read_json(timeline_path)
    if not isinstance(payload, list):
        raise RuntimeError(f"Timeline file must be a JSON array: {timeline_path}")

    valid_events = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if "event_id" not in item or "chronology_index" not in item:
            continue
        valid_events.append(item)

    if len(valid_events) < 2:
        raise RuntimeError(
            "Timeline has fewer than 2 valid events. "
            "Strict mode is enabled: no source -> no plan."
        )

    return sorted(valid_events, key=lambda ev: ev["chronology_index"])


def load_progression_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {"next_event_offset": 0}
    payload = read_json(state_path)
    if not isinstance(payload, dict):
        return {"next_event_offset": 0}
    raw_offset = payload.get("next_event_offset", 0)
    offset = raw_offset if isinstance(raw_offset, int) and raw_offset >= 0 else 0
    return {"next_event_offset": offset}


def save_progression_state(state_path: Path, next_offset: int, target_date: dt.date) -> None:
    payload = {
        "next_event_offset": next_offset,
        "last_plan_date": target_date.isoformat(),
        "updated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    write_json(state_path, payload)


def pick_next_two_events(events: list[dict], offset: int) -> tuple[dict, dict, int]:
    selected: list[dict] = []
    cursor = offset
    while cursor < len(events) and len(selected) < 2:
        event = events[cursor]
        actors = event.get("actors", [])
        if isinstance(actors, list) and len(actors) > 0:
            selected.append(event)
        cursor += 1

    if len(selected) < 2:
        raise RuntimeError(
            "Not enough remaining source events with actors to generate 2 main episodes. "
            "Add more normalized events with non-empty 'actors' in data/timeline/source_events.json."
        )

    return selected[0], selected[1], cursor


def build_plan(target_date: dt.date, event_a: dict, event_b: dict) -> dict:
    date_compact = target_date.strftime("%Y%m%d")

    main_1 = f"main-{date_compact}-linea_01"
    main_2 = f"main-{date_compact}-linea_02"

    teaser_1 = f"teaser-{date_compact}-linea_01"
    teaser_2 = f"teaser-{date_compact}-linea_02"
    story_a = build_main_storytelling(event_a)
    story_b = build_main_storytelling(event_b)
    teaser_story_a = build_teaser_storytelling({"hook": story_a["premise"], "scenes": story_a["scene_outline"]}, "cliffhanger")
    teaser_story_b = build_teaser_storytelling({"hook": story_b["premise"], "scenes": story_b["scene_outline"]}, "question")

    return {
        "plan_id": f"plan-{date_compact}",
        "date": target_date.isoformat(),
        "targets": {
            "main_count": 2,
            "teaser_count": 2,
        },
        "main_episodes": [
            {
                "episode_id": main_1,
                "source_event_ids": [event_a["event_id"]],
                "priority": 1,
                "target_duration_seconds": story_a["target_duration_seconds"],
                "storytelling": story_a,
            },
            {
                "episode_id": main_2,
                "source_event_ids": [event_b["event_id"]],
                "priority": 2,
                "target_duration_seconds": story_b["target_duration_seconds"],
                "storytelling": story_b,
            },
        ],
        "teaser_episodes": [
            {
                "episode_id": teaser_1,
                "parent_episode_id": main_1,
                "strategy": "cliffhanger",
                "target_duration_seconds": teaser_story_a["target_duration_seconds"],
                "storytelling": teaser_story_a,
            },
            {
                "episode_id": teaser_2,
                "parent_episode_id": main_2,
                "strategy": "question",
                "target_duration_seconds": teaser_story_b["target_duration_seconds"],
                "storytelling": teaser_story_b,
            },
        ],
        "notes": (
            "Plan generado en modo estricto con fuentes cronologicas. "
            f"Eventos usados: {event_a['event_id']}, {event_b['event_id']}."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        dest="plan_date",
        default=dt.date.today().isoformat(),
        help="Target date in YYYY-MM-DD format (default: today).",
    )
    parser.add_argument(
        "--timeline",
        default=str(TIMELINE_FILE),
        help="Path to normalized source events JSON file.",
    )
    parser.add_argument(
        "--state-file",
        default=str(PROGRESSION_FILE),
        help="Path to linear progression state JSON file.",
    )
    parser.add_argument(
        "--reset-progression",
        action="store_true",
        help="Reset linear progression and consume events from the beginning.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON file path (default: data/daily_plan/plan-YYYYMMDD.json)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing an existing output plan file.",
    )
    args = parser.parse_args()

    try:
        target_date = dt.date.fromisoformat(args.plan_date)
        timeline_path = Path(args.timeline)
        state_path = Path(args.state_file)

        events = load_timeline_events(timeline_path)
        progression = {"next_event_offset": 0} if args.reset_progression else load_progression_state(state_path)
        event_a, event_b, next_offset = pick_next_two_events(events, progression["next_event_offset"])

        plan = build_plan(target_date, event_a, event_b)

        default_output = ROOT / "data" / "daily_plan" / f"plan-{target_date.strftime('%Y%m%d')}.json"
        output_path = Path(args.output) if args.output else default_output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists() and not args.overwrite:
            raise RuntimeError(f"Output file already exists: {output_path}. Use --overwrite to replace it.")

        write_json(output_path, plan)
        save_progression_state(state_path, next_offset, target_date)

        print(f"Generated: {output_path}")
        print(f"Source events used: {event_a['event_id']}, {event_b['event_id']}")
        print(f"Progression next_event_offset: {next_offset} (state: {state_path})")
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
