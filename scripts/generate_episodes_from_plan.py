#!/usr/bin/env python3
"""Generate main and teaser episode JSON files from a daily plan.

Strict mode:
- Requires normalized source events
- Requires character files and timeline records for actors
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

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


def long_text(base: str, min_length: int = 20) -> str:
    if len(base) >= min_length:
        return base
    return (base + " " + "Contexto historico contrastado para continuidad narrativa.")[: max(min_length, 20)]


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
                "beat_summary": rec["change_summary"][:300],
            }
    raise RuntimeError(
        f"No timeline record for character '{character_id}' in event '{event_id}'. "
        "Character arc continuity requires per-event records."
    )


def build_main_scenes(event_title: str) -> list[dict]:
    # 4 scenes x 24s => 96s (fits main target 90-120s)
    return [
        {
            "scene_index": 1,
            "narration": long_text(
                f"{event_title}: el episodio arranca con un equilibrio fragil y presion creciente sobre los protagonistas."
            ),
            "visual_prompt": long_text(
                "Contexto altomedieval del norte peninsular, mapa, clima de incertidumbre y tension politica"
            ),
            "estimated_seconds": 24,
        },
        {
            "scene_index": 2,
            "narration": long_text(
                "Un movimiento tactico altera alianzas previas y abre una grieta en la version oficial de los hechos."
            ),
            "visual_prompt": long_text(
                "Consejo estrategico, lideres en conflicto, documento historico en primer plano"
            ),
            "estimated_seconds": 24,
        },
        {
            "scene_index": 3,
            "narration": long_text(
                "La fuente secundaria confirma el plot twist y obliga a reinterpretar decisiones clave del episodio."
            ),
            "visual_prompt": long_text(
                "Cronica iluminada, anotaciones marginales, atmosfera documental cinematografica"
            ),
            "estimated_seconds": 24,
        },
        {
            "scene_index": 4,
            "narration": long_text(
                "El cierre conecta el cambio emocional de los personajes con el siguiente tramo de la historia lineal."
            ),
            "visual_prompt": long_text(
                "Plano final epico sobrio con paisaje astur y continuidad narrativa"
            ),
            "estimated_seconds": 24,
        },
    ]


def create_main_episode(main_row: dict, events: dict[str, dict], char_dir: Path, timelines_dir: Path) -> dict:
    source_event_ids = main_row["source_event_ids"]
    lead_id = source_event_ids[0]
    if lead_id not in events:
        raise RuntimeError(f"Source event '{lead_id}' not found in timeline.")

    lead_event = events[lead_id]
    event_title = lead_event.get("title", f"Evento {lead_id}")
    location = lead_event.get("location", "Asturias")
    actors = lead_event.get("actors") or []
    if not actors:
        raise RuntimeError(f"Source event '{lead_id}' has no actors. Character continuity is required.")

    for actor in actors:
        load_character_file(actor, char_dir)

    character_beats = [beat_for_actor_event(actor, lead_id, timelines_dir) for actor in actors]
    scenes = build_main_scenes(event_title)
    duration = sum(scene["estimated_seconds"] for scene in scenes)

    hook = long_text(
        f"En {location}, una decision aparentemente menor dispara un giro que transforma la cronologia del episodio."
    )

    return {
        "episode_id": main_row["episode_id"],
        "episode_type": "main",
        "status": "generated",
        "language": "es",
        "timeline_event_ids": source_event_ids,
        "parent_episode_id": None,
        "title": long_text(f"{event_title}: el giro que cambia el relato", 8)[:120],
        "hook": hook[:300],
        "plot_twist": {
            "setup": long_text("La narracion dominante apunta a un desenlace previsible sin grandes variaciones.")[:400],
            "reveal": long_text("Una evidencia de fuente primaria altera la interpretacion y cambia el foco del conflicto.")[:400],
            "payoff": long_text("El cambio impacta tanto en los hechos como en el arco emocional de los protagonistas.")[:400],
        },
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
    strategy = teaser_row.get("strategy", "cliffhanger")

    line_by_strategy = {
        "cliffhanger": "El ultimo dato invierte todo lo que creias sobre este episodio.",
        "question": "Si esta no era la version oficial completa, quien decidio ocultar la pieza clave?",
        "quote": "Una frase de la cronica explica por que todo dio un giro inesperado.",
    }

    scenes = [
        {
            "scene_index": 1,
            "narration": long_text(parent_main["hook"][:240]),
            "visual_prompt": long_text("Recorte visual del momento de mayor tension narrativa"),
            "estimated_seconds": 13,
        },
        {
            "scene_index": 2,
            "narration": long_text(line_by_strategy.get(strategy, line_by_strategy["cliffhanger"])),
            "visual_prompt": long_text("Cierre teaser con CTA al episodio principal"),
            "estimated_seconds": 12,
        },
    ]

    return {
        "episode_id": teaser_row["episode_id"],
        "episode_type": "teaser",
        "status": "generated",
        "language": "es",
        "timeline_event_ids": parent_main["timeline_event_ids"][:1],
        "parent_episode_id": teaser_row["parent_episode_id"],
        "title": long_text(f"Teaser: {parent_main['title']}", 8)[:120],
        "hook": long_text(parent_main["hook"][:280])[:300],
        "scenes": scenes,
        "characters": parent_main["characters"][:2],
        "duration_seconds": 25,
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
