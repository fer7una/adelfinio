#!/usr/bin/env python3
"""Generate a linear story catalog from a source pack and character bible."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pipeline_common import (
    ROOT,
    build_openai_client,
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
)

PROMPT_VERSION = "story-catalog-v1"
DEFAULT_SOURCE_PACK = ROOT / "data" / "source" / "source_pack.json"
DEFAULT_CHARACTER_BIBLE = ROOT / "data" / "characters" / "character_bible.json"
DEFAULT_OUTPUT = ROOT / "data" / "story" / "story_catalog.json"
DEFAULT_MODEL_ENV = "OPENAI_STORY_CATALOG_MODEL"

SYSTEM_PROMPT = """
Eres un editor narrativo-historico. Debes proponer un catalogo lineal de episodios potenciales.
Reglas:
- Responde solo con JSON valido.
- Los stories deben seguir chronology_index ascendente.
- Cada historia debe poder elegirse manualmente sin romper continuidad.
- Separa observed_from_source e inferred_from_source.
- No propongas historias que contradigan la fuente revisada.
""".strip()


def print_llm_warning() -> None:
    print(
        "WARNING: generate_story_catalog.py uses OpenAI structured generation and will call the API.\n"
        "No built-in --mock is available for this narrative step.\n"
        "If you only need to avoid OpenAI calls during render, use --mock in run_story_pipeline_from_source.sh or run_final_ai_video_pipeline.sh.",
        file=os.sys.stderr,
    )


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


def ensure_reviewed(source_pack: dict) -> None:
    if (source_pack.get("review") or {}).get("status") != "approved":
        raise RuntimeError("source_pack review.status must be 'approved' before generating the story catalog.")


def compact_characters(character_bible: dict) -> list[dict]:
    items = []
    for character in character_bible.get("characters", []):
        items.append(
            {
                "character_id": character["character_id"],
                "display_name": character["display_name"],
                "role": character["role"],
                "behavior_patterns": (((character.get("observed_from_source") or {}).get("behavior_patterns")) or [])[:4],
                "likely_arc": (((character.get("inferred_from_source") or {}).get("likely_arc")) or [])[:4],
                "event_record_count": len(character.get("event_records") or []),
                "event_records": [
                    {
                        "event_id": record["event_id"],
                        "chronology_index": record["chronology_index"],
                        "change_summary": trim_text(str(record["change_summary"]), 180),
                    }
                    for record in sample_evenly(list(character.get("event_records") or []), 8)
                ],
            }
        )
    return items


def compact_events(source_pack: dict, limit: int = 240) -> list[dict]:
    events = sample_evenly(list(source_pack.get("derived_events", [])), limit)
    items = []
    for index, event in enumerate(events, start=1):
        items.append(
            {
                "sample_index": index,
                "event_id": event.get("event_id"),
                "date_start": event.get("date_start"),
                "title": trim_text(str(event.get("title") or ""), 120),
                "actors": list((event.get("actors") or [])[:6]),
                "location": trim_text(str(event.get("location") or ""), 80),
                "summary": trim_text(str(event.get("summary") or ""), 180),
            }
        )
    return items


def compact_chronology_hints(source_pack: dict, limit: int = 120) -> list[dict]:
    hints = sample_evenly(list(source_pack.get("chronology_hints", [])), limit)
    return [
        {
            "chunk_id": hint.get("chunk_id"),
            "detected_years": list((hint.get("detected_years") or [])[:6]),
            "summary": trim_text(str(hint.get("summary") or ""), 180),
        }
        for hint in hints
    ]


def prompt_payload(source_pack: dict, character_bible: dict) -> str:
    payload = {
        "source_pack_id": source_pack["source_pack_id"],
        "character_bible_id": character_bible["character_bible_id"],
        "source_stats": {
            "page_count": len(source_pack.get("pages", [])),
            "chunk_count": len(source_pack.get("chunks", [])),
            "derived_event_count": len(source_pack.get("derived_events", [])),
        },
        "derived_event_samples": compact_events(source_pack),
        "chronology_hints": compact_chronology_hints(source_pack),
        "characters": compact_characters(character_bible),
        "instructions": {
            "selection_mode": "manual",
            "goal": "Devolver episodios potenciales lineales, comparables y fieles a la fuente.",
            "sampling_policy": "uniform_coverage_over_full_source",
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-pack", default=str(DEFAULT_SOURCE_PACK), help="Approved source_pack.json path")
    parser.add_argument("--character-bible", default=str(DEFAULT_CHARACTER_BIBLE), help="character_bible.json path")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output story_catalog.json path")
    parser.add_argument("--dotenv", default=str(ROOT / ".env"), help="Path to dotenv file")
    parser.add_argument("--model", default=None, help=f"OpenAI model for catalog generation (env: {DEFAULT_MODEL_ENV})")
    parser.add_argument("--reasoning-effort", default="medium", help="OpenAI reasoning effort")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting outputs")
    args = parser.parse_args()

    source_pack_path = Path(args.source_pack)
    character_bible_path = Path(args.character_bible)
    output_path = Path(args.output)

    if not source_pack_path.exists():
        print(f"ERROR: source pack not found: {source_pack_path}")
        return 1
    if not character_bible_path.exists():
        print(f"ERROR: character bible not found: {character_bible_path}")
        return 1
    if output_path.exists() and not args.overwrite:
        print(f"ERROR: output already exists: {output_path}. Use --overwrite.")
        return 1

    try:
        source_pack = read_json(source_pack_path)
        character_bible = read_json(character_bible_path)
        ensure_reviewed(source_pack)
        load_dotenv_if_present(Path(args.dotenv))
        model = args.model or os.getenv(DEFAULT_MODEL_ENV) or os.getenv("OPENAI_STORY_MODEL") or "gpt-5.4"
        schema = inline_external_schema_refs(load_schema("story_catalog.schema.json"))
        prompt_body = prompt_payload(source_pack, character_bible)
        print_llm_warning()
        client = build_openai_client(Path(args.dotenv))
        payload = run_structured_generation(
            client=client,
            model=model,
            schema_name="story_catalog",
            schema=schema,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt_body,
            reasoning_effort=args.reasoning_effort,
        )
        payload["story_catalog_id"] = payload.get("story_catalog_id") or f"sc-{source_pack['source_pack_id'][4:]}"
        payload["source_pack_id"] = source_pack["source_pack_id"]
        payload["character_bible_id"] = character_bible["character_bible_id"]
        payload["selection_mode"] = "manual"
        payload["generation_meta"] = default_generation_meta(
            model=model,
            prompt_version=PROMPT_VERSION,
            prompt_body=SYSTEM_PROMPT + "\n" + prompt_body,
            input_checksum=sha256_text(normalize_ws(prompt_body)),
        )
        write_json(output_path, payload)
        print(f"Generated story catalog: {output_path}")
        for story in payload.get("stories", [])[:10]:
            print(f"- {story['story_id']}: {story['title']}")
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
