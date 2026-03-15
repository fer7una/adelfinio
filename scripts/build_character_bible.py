#!/usr/bin/env python3
"""Build character files and emotional arc timelines from source events."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVENTS = ROOT / "data" / "timeline" / "source_events.json"
DEFAULT_CHAR_DIR = ROOT / "data" / "characters"
DEFAULT_TIMELINE_DIR = ROOT / "data" / "characters" / "timelines"

EMOTIONS = [
    "calm",
    "hope",
    "fear",
    "anger",
    "sadness",
    "resolve",
    "joy",
    "grief",
    "pride",
    "shame",
    "uncertainty",
]

ARC_STAGES = ["setup", "ascent", "crisis", "turn", "resolution", "legacy"]

NEGATIVE_HINTS = ["derrota", "muerte", "crisis", "asedio", "miedo", "amenaza", "perdida"]
POSITIVE_HINTS = ["victoria", "alianza", "acuerdo", "avance", "consolid", "legitim"]
RESOLVE_HINTS = ["resistencia", "organiza", "lider", "reaccion", "estrateg"]
INCOMPLETE_TAIL_TOKENS = {
    "a",
    "al",
    "con",
    "de",
    "del",
    "el",
    "en",
    "la",
    "las",
    "los",
    "o",
    "para",
    "por",
    "que",
    "sin",
    "su",
    "sus",
    "un",
    "una",
    "y",
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def actor_to_display_name(actor_id: str) -> str:
    parts = actor_id.split("_")
    if parts and parts[-1] in {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}:
        return " ".join([p.capitalize() for p in parts[:-1]] + [parts[-1].upper()])
    return " ".join(p.capitalize() for p in parts)


def infer_emotion(summary: str) -> tuple[str, float, float, float]:
    low = summary.lower()
    if any(h in low for h in NEGATIVE_HINTS):
        return ("fear", 0.72, -0.55, 0.70)
    if any(h in low for h in RESOLVE_HINTS):
        return ("resolve", 0.78, 0.20, 0.76)
    if any(h in low for h in POSITIVE_HINTS):
        return ("pride", 0.68, 0.46, 0.62)
    return ("uncertainty", 0.52, -0.10, 0.55)


def stage_for_position(position: int, total: int) -> str:
    if total <= 1:
        return "setup"
    ratio = position / total
    if ratio <= 0.17:
        return "setup"
    if ratio <= 0.40:
        return "ascent"
    if ratio <= 0.62:
        return "crisis"
    if ratio <= 0.80:
        return "turn"
    if ratio <= 0.95:
        return "resolution"
    return "legacy"


def parse_year(date_start: str) -> int:
    m = re.match(r"^(\d{4})", date_start)
    return int(m.group(1)) if m else 0


def trim_text(text: str, max_len: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_len:
        return compact
    cut = compact[: max_len + 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    else:
        cut = cut[:max_len]
    return cut.rstrip(".,;: ") + "..."


def fallback_event_context(event: dict) -> str:
    event_id = event.get("event_id", "evento")
    date_start = event.get("date_start", "s/f")
    location = event.get("location", "ubicacion no definida")
    return f"evento {event_id} ({date_start}, {location})"


def event_context_label(event: dict) -> str:
    title = re.sub(r"\s+", " ", str(event.get("title", "")).strip())
    title = title.rstrip(" .,:;!-")
    if not title or len(title) < 12:
        return fallback_event_context(event)
    words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", title)
    if words and words[-1].lower() in INCOMPLETE_TAIL_TOKENS:
        return fallback_event_context(event)
    return title


def build_character_record(actor_id: str, events: list[dict], timeline_records: list[dict]) -> dict:
    years = [parse_year(e.get("date_start", "")) for e in events]
    years = [y for y in years if y > 0]
    first_year = min(years) if years else 0
    last_year = max(years) if years else 0
    latest_event = sorted(events, key=lambda e: e["chronology_index"])[-1]
    last_arc = timeline_records[-1]

    return {
        "character_id": actor_id,
        "display_name": actor_to_display_name(actor_id),
        "aliases": [],
        "historical_period": f"{first_year}-{last_year}" if first_year and last_year else "Periodo por definir",
        "role": "other",
        "biography_short": (
            f"Personaje recurrente en la cronologia del reino de Asturias. "
            f"Interviene en {len(events)} eventos documentados de la fuente canonica."
        ),
        "visual_profile": {
            "age_range": "30-50",
            "hair": "castano",
            "beard": "variable segun etapa",
            "clothing": "vestimenta altomedieval del norte peninsular",
            "palette": ["#4D5A6A", "#8D6B46", "#A9B2C0"],
            "style_notes": "Priorizar coherencia historica y evitar anacronismos visuales."
        },
        "state": {
            "status": "active",
            "location": latest_event.get("location", "Asturias"),
            "alliances": [],
            "conflicts": [],
            "last_seen_episode_id": None,
            "last_updated": now_iso()
        },
        "continuity": {
            "must_keep": [
                "Consistencia de motivaciones entre episodios consecutivos",
                "Coherencia historica con las referencias de fuente"
            ],
            "must_avoid": [
                "Cambios bruscos de personalidad sin evidencia en fuente",
                "Lenguaje anacronico"
            ]
        },
        "emotional_state": {
            "dominant_emotion": last_arc["emotion_after"],
            "intensity": 0.70,
            "valence": 0.22,
            "arousal": 0.68,
            "trigger_summary": trim_text(last_arc["change_summary"], 300)
        },
        "arc_state": {
            "stage": last_arc["arc_stage_after"],
            "external_goal": "Sostener su posicion en la secuencia politica y militar.",
            "internal_need": "Alinear decision personal con estabilidad colectiva.",
            "contradiction": "Interes inmediato frente a continuidad de largo plazo.",
            "change_vector": "Evolucion gradual guiada por eventos documentados.",
            "last_event_id": last_arc["event_id"]
        }
    }


def build_timelines(events: list[dict]) -> dict[str, list[dict]]:
    actor_events: dict[str, list[dict]] = {}
    for event in sorted(events, key=lambda e: e["chronology_index"]):
        for actor in event.get("actors", []):
            actor_events.setdefault(actor, []).append(event)

    timelines: dict[str, list[dict]] = {}
    for actor_id, seq in actor_events.items():
        records: list[dict] = []
        prev_emotion = "uncertainty"
        prev_stage = "setup"
        total = len(seq)

        for pos, event in enumerate(seq, start=1):
            after_emotion, _intensity, _valence, _arousal = infer_emotion(event.get("summary", ""))
            after_stage = stage_for_position(pos, total)
            event_context = event_context_label(event)
            if event_context.startswith("evento "):
                change_summary = (
                    f"{actor_to_display_name(actor_id)} evoluciona de {prev_emotion} a {after_emotion} "
                    f"en el {event_context}."
                )
            else:
                change_summary = (
                    f"{actor_to_display_name(actor_id)} evoluciona de {prev_emotion} a {after_emotion} "
                    f"durante {event_context}."
                )
            rec = {
                "record_id": f"arc-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d')}-{actor_id}_{pos:02d}",
                "event_id": event["event_id"],
                "chronology_index": event["chronology_index"],
                "emotion_before": prev_emotion,
                "emotion_after": after_emotion,
                "arc_stage_before": prev_stage,
                "arc_stage_after": after_stage,
                "change_summary": trim_text(change_summary, 300),
                "source_ref": {
                    "file": event["source_ref"]["file"],
                    "section": event["source_ref"]["section"],
                    "checksum": event["source_ref"]["checksum"],
                    "excerpt": trim_text(str(event.get("summary", "")), 220)
                },
                "updated_at": now_iso()
            }
            records.append(rec)
            prev_emotion = after_emotion
            prev_stage = after_stage

        timelines[actor_id] = records
    return timelines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", default=str(DEFAULT_EVENTS), help="Normalized source events JSON path")
    parser.add_argument("--characters-dir", default=str(DEFAULT_CHAR_DIR), help="Output directory for character files")
    parser.add_argument("--timelines-dir", default=str(DEFAULT_TIMELINE_DIR), help="Output directory for character timeline files")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwrite existing character/timeline files")
    args = parser.parse_args()

    events_path = Path(args.events)
    characters_dir = Path(args.characters_dir)
    timelines_dir = Path(args.timelines_dir)

    if not events_path.exists():
        print(f"ERROR: events file not found: {events_path}")
        return 1

    events = read_json(events_path)
    if not isinstance(events, list) or not events:
        print("ERROR: events payload is empty or invalid.")
        return 1

    timelines = build_timelines(events)
    if not timelines:
        print("ERROR: no actors found in events. Ensure 'actors' exists in source events.")
        return 1

    for actor_id, records in timelines.items():
        actor_events = [e for e in events if actor_id in e.get("actors", [])]
        char_record = build_character_record(actor_id, actor_events, records)

        char_file = characters_dir / f"{actor_id}.json"
        timeline_file = timelines_dir / f"{actor_id}.json"

        if (char_file.exists() or timeline_file.exists()) and not args.overwrite:
            print(f"ERROR: {actor_id} already exists. Use --overwrite.")
            return 1

        write_json(char_file, char_record)
        write_json(timeline_file, {"character_id": actor_id, "records": records})

    print(f"Generated {len(timelines)} character files in {characters_dir}")
    print(f"Generated {len(timelines)} timeline files in {timelines_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
