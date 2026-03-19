#!/usr/bin/env python3
"""Generate a linear story catalog from a source pack and character bible."""

from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import json
import os
import re
import unicodedata
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

NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def print_llm_warning() -> None:
    print(
        "WARNING: generate_story_catalog.py uses OpenAI structured generation and will call the API.\n"
        "No built-in --mock is available for this narrative step.\n"
        "If you only need to avoid OpenAI calls during render, use --mock in run_story_pipeline_from_source.sh or run_final_ai_video_pipeline_v2.sh.",
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


def normalize_ref_text(value: str) -> str:
    folded = unicodedata.normalize("NFKD", normalize_ws(value)).encode("ascii", "ignore").decode("ascii")
    return NON_WORD_RE.sub(" ", folded.lower()).strip()


def story_source_ref_from_chunk(chunk: dict) -> dict:
    section = trim_text(str(chunk.get("normalized_text") or chunk.get("heading") or chunk.get("chunk_id") or ""), 120)
    excerpt = trim_text(str(chunk.get("normalized_text") or chunk.get("text") or section), 260)
    return {
        "file": str(chunk.get("file") or ""),
        "section": section,
        "checksum": str(chunk.get("checksum") or ""),
        "excerpt": excerpt,
        "page_start": chunk.get("page_start"),
        "page_end": chunk.get("page_end"),
        "chunk_id": chunk.get("chunk_id"),
    }


def build_source_indexes(source_pack: dict) -> dict:
    derived_events = [event for event in source_pack.get("derived_events", []) if isinstance(event, dict)]
    chunks = [chunk for chunk in source_pack.get("chunks", []) if isinstance(chunk, dict)]
    event_by_id = {str(event.get("event_id")): event for event in derived_events if event.get("event_id")}
    event_by_chunk_id = {
        str(event.get("source_ref", {}).get("section")): event
        for event in derived_events
        if isinstance(event.get("source_ref"), dict) and event.get("source_ref", {}).get("section")
    }
    chunk_by_id = {str(chunk.get("chunk_id")): chunk for chunk in chunks if chunk.get("chunk_id")}
    normalized_chunk_texts = []
    for chunk in chunks:
        norm = normalize_ref_text(str(chunk.get("normalized_text") or chunk.get("text") or chunk.get("heading") or ""))
        if not norm:
            continue
        normalized_chunk_texts.append((norm, chunk))
    return {
        "event_by_id": event_by_id,
        "event_by_chunk_id": event_by_chunk_id,
        "chunk_by_id": chunk_by_id,
        "normalized_chunk_texts": normalized_chunk_texts,
    }


def resolve_chunk_from_ref(ref: dict, indexes: dict) -> dict | None:
    chunk_id = ref.get("chunk_id")
    chunk_by_id = indexes["chunk_by_id"]
    if isinstance(chunk_id, str) and chunk_id in chunk_by_id:
        return chunk_by_id[chunk_id]

    candidates = [normalize_ref_text(str(ref.get("section") or "")), normalize_ref_text(str(ref.get("excerpt") or ""))]
    candidates = [candidate for candidate in candidates if candidate]
    if not candidates:
        return None

    normalized_chunk_texts = indexes["normalized_chunk_texts"]
    for candidate in candidates:
        for chunk_text, chunk in normalized_chunk_texts:
            if candidate == chunk_text or candidate in chunk_text or chunk_text in candidate:
                return chunk

    best_score = 0.0
    best_chunk = None
    for candidate in candidates:
        for chunk_text, chunk in normalized_chunk_texts:
            prefix = chunk_text[: max(len(candidate) + 32, len(chunk_text))]
            score = SequenceMatcher(None, candidate, prefix).ratio()
            if score > best_score:
                best_score = score
                best_chunk = chunk
    if best_score >= 0.72:
        return best_chunk
    return None


def resolve_event_id_candidates(story: dict, indexes: dict) -> tuple[list[str], list[dict]]:
    event_by_id = indexes["event_by_id"]
    event_by_chunk_id = indexes["event_by_chunk_id"]
    chunk_by_id = indexes["chunk_by_id"]

    resolved_event_ids: list[str] = []
    resolved_chunks: list[dict] = []
    seen_event_ids: set[str] = set()
    seen_chunk_ids: set[str] = set()

    def add_event(event: dict | None, chunk: dict | None = None) -> None:
        if not event:
            return
        event_id = str(event.get("event_id") or "")
        if not event_id or event_id in seen_event_ids:
            return
        seen_event_ids.add(event_id)
        resolved_event_ids.append(event_id)
        if chunk is None:
            chunk = chunk_by_id.get(str(event.get("source_ref", {}).get("section") or ""))
        if chunk:
            chunk_id = str(chunk.get("chunk_id") or "")
            if chunk_id and chunk_id not in seen_chunk_ids:
                seen_chunk_ids.add(chunk_id)
                resolved_chunks.append(chunk)

    for raw_event_id in story.get("source_event_ids", []):
        if raw_event_id in event_by_id:
            add_event(event_by_id[raw_event_id])
            continue
        if isinstance(raw_event_id, str):
            digits = raw_event_id.removeprefix("evt-")
            if len(digits) == 4:
                add_event(event_by_chunk_id.get(f"chk-{digits}"))

    for ref in story.get("source_refs", []):
        if not isinstance(ref, dict):
            continue
        chunk = resolve_chunk_from_ref(ref, indexes)
        if not chunk:
            continue
        event = event_by_chunk_id.get(str(chunk.get("chunk_id") or ""))
        add_event(event, chunk)

    return resolved_event_ids, resolved_chunks


def sanitize_story_catalog_payload(source_pack: dict, payload: dict) -> dict:
    indexes = build_source_indexes(source_pack)
    for story in payload.get("stories", []):
        if not isinstance(story, dict):
            continue
        resolved_event_ids, resolved_chunks = resolve_event_id_candidates(story, indexes)
        if not resolved_event_ids:
            raise RuntimeError(f"Story '{story.get('story_id')}' could not be linked to any real derived event.")
        story["source_event_ids"] = resolved_event_ids
        story["source_refs"] = [story_source_ref_from_chunk(chunk) for chunk in resolved_chunks] or [
            story_source_ref_from_chunk(indexes["chunk_by_id"][indexes["event_by_id"][event_id]["source_ref"]["section"]])
            for event_id in resolved_event_ids
            if indexes["event_by_id"][event_id]["source_ref"]["section"] in indexes["chunk_by_id"]
        ]
    return payload


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
        payload = sanitize_story_catalog_payload(source_pack, payload)
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
