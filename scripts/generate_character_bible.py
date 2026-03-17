#!/usr/bin/env python3
"""Generate a consolidated character bible from an approved source pack."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pipeline_common import (
    ROOT,
    default_generation_meta,
    inline_external_schema_refs,
    load_dotenv_if_present,
    load_schema,
    normalize_ws,
    read_json,
    run_structured_generation,
    sha256_text,
    trim_text,
    write_json,
    build_openai_client,
)

PROMPT_VERSION = "character-bible-v1"
DEFAULT_SOURCE_PACK = ROOT / "data" / "source" / "source_pack.json"
DEFAULT_OUTPUT = ROOT / "data" / "characters" / "character_bible.json"
DEFAULT_CHAR_DIR = ROOT / "data" / "characters"
DEFAULT_TIMELINES_DIR = ROOT / "data" / "characters" / "timelines"
DEFAULT_MODEL_ENV = "OPENAI_CHARACTER_MODEL"

SYSTEM_PROMPT = """
Eres un analista narrativo-historico. Debes producir un character bible estructurado y auditable.
Reglas:
- Responde solo con JSON valido.
- Separa siempre observed_from_source e inferred_from_source.
- No inventes hechos no sostenidos por la fuente.
- Toda inferencia debe apoyarse en source_refs y confidence.
- Modela comportamiento, voz, visualidad y arco pensando en su uso posterior en video.
- Mantén continuidad cronologica y emocional por personaje.
""".strip()


def sample_evenly(items: list[dict], limit: int) -> list[dict]:
    if limit <= 0 or len(items) <= limit:
        return list(items)
    if limit == 1:
        return [items[0]]
    step = (len(items) - 1) / (limit - 1)
    picked: list[dict] = []
    used: set[int] = set()
    for index in range(limit):
        pos = round(index * step)
        if pos in used:
            continue
        used.add(pos)
        picked.append(items[pos])
    return picked


def build_actor_candidates(source_pack: dict, limit: int = 80) -> list[dict]:
    actor_map: dict[str, dict] = {}
    for event in source_pack.get("derived_events", []):
        for actor in event.get("actors", []) or []:
            name = normalize_ws(str(actor))
            if len(name) < 3:
                continue
            entry = actor_map.setdefault(
                name,
                {
                    "name": name,
                    "mentions": 0,
                    "sample_events": [],
                    "years": set(),
                },
            )
            entry["mentions"] += 1
            if len(entry["sample_events"]) < 4:
                entry["sample_events"].append(
                    {
                        "event_id": event.get("event_id"),
                        "title": trim_text(str(event.get("title") or event.get("summary") or ""), 120),
                    }
                )
            year = str(event.get("date_start") or "").strip()
            if year:
                entry["years"].add(year)

    ranked = sorted(actor_map.values(), key=lambda item: (-item["mentions"], item["name"]))
    out: list[dict] = []
    for item in ranked[:limit]:
        out.append(
            {
                "name": item["name"],
                "mentions": item["mentions"],
                "sample_years": sorted(item["years"])[:6],
                "sample_events": item["sample_events"],
            }
        )
    return out


def compact_event_samples(source_pack: dict, limit: int = 180) -> list[dict]:
    events = sample_evenly(list(source_pack.get("derived_events", [])), limit)
    return [
        {
            "event_id": event.get("event_id"),
            "date_start": event.get("date_start"),
            "title": trim_text(str(event.get("title") or ""), 120),
            "actors": list((event.get("actors") or [])[:6]),
            "location": trim_text(str(event.get("location") or ""), 80),
            "summary": trim_text(str(event.get("summary") or ""), 180),
        }
        for event in events
    ]


def compact_chunk_samples(source_pack: dict, limit: int = 120) -> list[dict]:
    chunks = sample_evenly(list(source_pack.get("chunks", [])), limit)
    return [
        {
            "chunk_id": chunk.get("chunk_id"),
            "page_start": chunk.get("page_start"),
            "page_end": chunk.get("page_end"),
            "ocr_confidence": chunk.get("ocr_confidence"),
            "issues": list((chunk.get("issues") or [])[:4]),
            "text": trim_text(str(chunk.get("normalized_text") or ""), 220),
        }
        for chunk in chunks
    ]


def prompt_payload(source_pack: dict) -> str:
    payload = {
        "source_pack_id": source_pack["source_pack_id"],
        "title": source_pack["title"],
        "review": source_pack["review"],
        "source_stats": {
            "page_count": len(source_pack.get("pages", [])),
            "chunk_count": len(source_pack.get("chunks", [])),
            "derived_event_count": len(source_pack.get("derived_events", [])),
        },
        "actor_candidates": build_actor_candidates(source_pack),
        "derived_event_samples": compact_event_samples(source_pack),
        "chunk_samples": compact_chunk_samples(source_pack),
        "instructions": {
            "goal": "Extraer personajes, comportamiento, relaciones y continuidad dramatica sin perder fidelidad.",
            "inference_policy": "evidence_plus_labeled_inference",
            "sampling_policy": "uniform_coverage_plus_actor_index",
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def ensure_reviewed(source_pack: dict) -> None:
    review = source_pack.get("review") or {}
    if review.get("status") != "approved":
        raise RuntimeError("source_pack review.status must be 'approved' before generating characters.")


def alliances_from_relationships(character_payload: dict, kinds: set[str]) -> list[str]:
    relationships = (((character_payload.get("observed_from_source") or {}).get("relationships")) or [])
    out: list[str] = []
    for relation in relationships:
        target = str(relation.get("target_character_id", "")).strip()
        rel_type = str(relation.get("relationship_type", "")).strip()
        if target and rel_type in kinds and target not in out:
            out.append(target)
    return out


def materialize_character_record(payload: dict) -> dict:
    state = dict(payload.get("state") or {})
    state["alliances"] = alliances_from_relationships(payload, {"ally", "family", "authority", "subordinate"})
    state["conflicts"] = alliances_from_relationships(payload, {"enemy", "rival"})
    return {
        "character_id": payload["character_id"],
        "display_name": payload["display_name"],
        "aliases": list(payload.get("aliases") or []),
        "historical_period": payload["historical_period"],
        "role": payload["role"],
        "biography_short": payload["biography_short"],
        "voice_profile": dict(payload["voice_profile"]),
        "visual_profile": dict(payload["visual_profile"]),
        "state": state,
        "continuity": dict(payload["continuity"]),
        "emotional_state": dict(payload["emotional_state"]),
        "arc_state": dict(payload["arc_state"]),
    }


def materialize_timeline_record(record: dict) -> dict:
    first_ref = dict((record.get("source_refs") or [])[0])
    return {
        "record_id": record["record_id"],
        "event_id": record["event_id"],
        "chronology_index": record["chronology_index"],
        "emotion_before": record["emotion_before"],
        "emotion_after": record["emotion_after"],
        "arc_stage_before": record["arc_stage_before"],
        "arc_stage_after": record["arc_stage_after"],
        "change_summary": trim_text(str(record["change_summary"]), 300),
        "source_ref": {
            "file": first_ref["file"],
            "section": first_ref["section"],
            "checksum": first_ref["checksum"],
            "excerpt": trim_text(str(first_ref.get("excerpt", record["change_summary"])), 220),
        },
        "updated_at": record["updated_at"],
    }


def write_compat_outputs(character_bible: dict, characters_dir: Path, timelines_dir: Path, overwrite: bool) -> None:
    for character in character_bible.get("characters", []):
        char_path = characters_dir / f"{character['character_id']}.json"
        timeline_path = timelines_dir / f"{character['character_id']}.json"
        if (char_path.exists() or timeline_path.exists()) and not overwrite:
            raise RuntimeError(f"Compatibility output already exists for {character['character_id']}. Use --overwrite.")
        write_json(char_path, materialize_character_record(character))
        write_json(
            timeline_path,
            {
                "character_id": character["character_id"],
                "records": [materialize_timeline_record(record) for record in character.get("event_records", [])],
            },
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-pack", default=str(DEFAULT_SOURCE_PACK), help="Approved source_pack.json path")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output character_bible.json path")
    parser.add_argument("--characters-dir", default=str(DEFAULT_CHAR_DIR), help="Compatibility character JSON directory")
    parser.add_argument("--timelines-dir", default=str(DEFAULT_TIMELINES_DIR), help="Compatibility timelines directory")
    parser.add_argument("--dotenv", default=str(ROOT / ".env"), help="Path to dotenv file")
    parser.add_argument("--model", default=None, help=f"OpenAI model for character generation (env: {DEFAULT_MODEL_ENV})")
    parser.add_argument("--reasoning-effort", default="medium", help="OpenAI reasoning effort")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting outputs")
    args = parser.parse_args()

    source_pack_path = Path(args.source_pack)
    output_path = Path(args.output)
    characters_dir = Path(args.characters_dir)
    timelines_dir = Path(args.timelines_dir)

    if not source_pack_path.exists():
        print(f"ERROR: source pack not found: {source_pack_path}")
        return 1
    if output_path.exists() and not args.overwrite:
        print(f"ERROR: output already exists: {output_path}. Use --overwrite.")
        return 1

    try:
        source_pack = read_json(source_pack_path)
        ensure_reviewed(source_pack)
        load_dotenv_if_present(Path(args.dotenv))
        model = args.model or os.getenv(DEFAULT_MODEL_ENV) or os.getenv("OPENAI_STORY_MODEL") or "gpt-5.4"
        schema = inline_external_schema_refs(load_schema("character_bible.schema.json"))
        prompt_body = prompt_payload(source_pack)
        client = build_openai_client(Path(args.dotenv))
        payload = run_structured_generation(
            client=client,
            model=model,
            schema_name="character_bible",
            schema=schema,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt_body,
            reasoning_effort=args.reasoning_effort,
        )
        payload["character_bible_id"] = payload.get("character_bible_id") or f"cb-{source_pack['source_pack_id'][4:]}"
        payload["source_pack_id"] = source_pack["source_pack_id"]
        payload["created_at"] = payload.get("created_at") or source_pack.get("created_at")
        payload["inference_policy"] = "evidence_plus_labeled_inference"
        payload["generation_meta"] = default_generation_meta(
            model=model,
            prompt_version=PROMPT_VERSION,
            prompt_body=SYSTEM_PROMPT + "\n" + prompt_body,
            input_checksum=sha256_text(normalize_ws(prompt_body)),
        )
        write_json(output_path, payload)
        write_compat_outputs(payload, characters_dir, timelines_dir, overwrite=args.overwrite)
        print(f"Generated character bible: {output_path}")
        print(f"Compatibility characters: {characters_dir}")
        print(f"Compatibility timelines: {timelines_dir}")
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
