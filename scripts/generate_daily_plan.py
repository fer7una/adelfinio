#!/usr/bin/env python3
"""Generate a daily plan with 2 main episodes and 2 teaser episodes."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMELINE_FILE = ROOT / "data" / "timeline" / "source_events.json"


def load_event_ids() -> list[str]:
    if TIMELINE_FILE.exists():
        with TIMELINE_FILE.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, list):
            sorted_events = sorted(payload, key=lambda ev: ev.get("chronology_index", 999999))
            event_ids = [ev["event_id"] for ev in sorted_events if "event_id" in ev]
            if len(event_ids) >= 2:
                return event_ids[:2]
    return ["evt-1001", "evt-1002"]


def build_plan(target_date: dt.date) -> dict:
    date_compact = target_date.strftime("%Y%m%d")
    event_a, event_b = load_event_ids()

    main_1 = f"main-{date_compact}-linea_01"
    main_2 = f"main-{date_compact}-linea_02"

    teaser_1 = f"teaser-{date_compact}-linea_01"
    teaser_2 = f"teaser-{date_compact}-linea_02"

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
                "source_event_ids": [event_a],
                "priority": 1,
            },
            {
                "episode_id": main_2,
                "source_event_ids": [event_b],
                "priority": 2,
            },
        ],
        "teaser_episodes": [
            {
                "episode_id": teaser_1,
                "parent_episode_id": main_1,
                "strategy": "cliffhanger",
            },
            {
                "episode_id": teaser_2,
                "parent_episode_id": main_2,
                "strategy": "question",
            },
        ],
        "notes": "Plan generado automaticamente. TODO: priorizacion dinamica por rendimiento historico.",
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
        "--output",
        default=None,
        help="Output JSON file path (default: data/daily_plan/plan-YYYYMMDD.json)",
    )
    args = parser.parse_args()

    target_date = dt.date.fromisoformat(args.plan_date)
    plan = build_plan(target_date)

    default_output = ROOT / "data" / "daily_plan" / f"plan-{target_date.strftime('%Y%m%d')}.json"
    output_path = Path(args.output) if args.output else default_output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(plan, handle, indent=2, ensure_ascii=True)
        handle.write("\n")

    print(f"Generated: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
