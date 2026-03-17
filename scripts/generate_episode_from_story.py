#!/usr/bin/env python3
"""Generate a final dramatic episode JSON from a selected story."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pipeline_common import (
    ROOT,
    build_openai_client,
    default_generation_meta,
    load_dotenv_if_present,
    load_schema,
    now_iso,
    normalize_ws,
    read_json,
    run_structured_generation,
    sha256_text,
    trim_text,
    write_json,
)

PROMPT_VERSION = "episode-drama-v1"
DEFAULT_SOURCE_PACK = ROOT / "data" / "source" / "source_pack.json"
DEFAULT_CHARACTER_BIBLE = ROOT / "data" / "characters" / "character_bible.json"
DEFAULT_STORY_CATALOG = ROOT / "data" / "story" / "story_catalog.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "episodes" / "generated" / "story-catalog"
DEFAULT_MODEL_ENV = "OPENAI_EPISODE_MODEL"

SYSTEM_PROMPT = """
Eres un guionista narrativo-historico para video vertical.
Reglas:
- Responde solo con JSON valido que cumpla el contrato de episode.
- Construye escenas visuales y dramaticas sin contradecir la fuente.
- Marca toda interioridad, subtexto o inferencia en inference_flags y source_evidence.
- Mantén continuidad emocional y de arco de personajes.
- Escribe narracion y dialogo listos para render, en espanol, sin anacronismos.
""".strip()


def ensure_reviewed(source_pack: dict) -> None:
    if (source_pack.get("review") or {}).get("status") != "approved":
        raise RuntimeError("source_pack review.status must be 'approved' before generating episodes.")


def find_story(story_catalog: dict, story_id: str) -> dict:
    for story in story_catalog.get("stories", []):
        if story.get("story_id") == story_id:
            return story
    raise RuntimeError(f"story_id not found in story catalog: {story_id}")


def relevant_chunks(source_pack: dict, story: dict) -> list[dict]:
    chunk_ids = {ref.get("chunk_id") for ref in story.get("source_refs", []) if ref.get("chunk_id")}
    chunks = []
    for chunk in source_pack.get("chunks", []):
        if not chunk_ids or chunk.get("chunk_id") in chunk_ids:
            chunks.append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "page_start": chunk["page_start"],
                    "page_end": chunk["page_end"],
                    "text": trim_text(str(chunk["normalized_text"]), 900),
                    "issues": chunk.get("issues", []),
                }
            )
    return chunks[:24]


def relevant_characters(character_bible: dict, story: dict) -> list[dict]:
    wanted = set(story.get("characters", []))
    out = []
    for character in character_bible.get("characters", []):
        if character["character_id"] not in wanted:
            continue
        out.append(
            {
                "character_id": character["character_id"],
                "display_name": character["display_name"],
                "role": character["role"],
                "continuity": character.get("continuity", {}),
                "emotional_state": character.get("emotional_state", {}),
                "arc_state": character.get("arc_state", {}),
                "observed_from_source": character.get("observed_from_source", {}),
                "inferred_from_source": character.get("inferred_from_source", {}),
                "event_records": character.get("event_records", []),
            }
        )
    return out


def prompt_payload(source_pack: dict, character_bible: dict, story_catalog: dict, story: dict) -> str:
    payload = {
        "source_pack_id": source_pack["source_pack_id"],
        "character_bible_id": character_bible["character_bible_id"],
        "story_catalog_id": story_catalog["story_catalog_id"],
        "selected_story": story,
        "relevant_chunks": relevant_chunks(source_pack, story),
        "relevant_characters": relevant_characters(character_bible, story),
        "instructions": {
            "episode_type": "main",
            "target_duration_seconds": "90-150",
            "goal": "Generar un episodio dramatico final, fiel a la fuente y listo para render.",
            "required_episode_fields": [
                "plot_twist",
                "character_beats",
                "source_evidence",
                "prompt_rationale",
                "inference_flags"
            ],
            "required_scene_fields": [
                "scene_cast",
                "scene_objective",
                "source_evidence",
                "character_state_before",
                "character_state_after",
                "behavioral_notes",
                "subtext_or_private_state_summary",
                "prompt_rationale",
                "inference_flags"
            ]
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def normalize_episode(payload: dict, story: dict) -> dict:
    story_slug = str(story["story_id"]).replace("story-", "")[:48]
    payload["episode_id"] = payload.get("episode_id") or f"main-{now_iso()[:10].replace('-', '')}-{story_slug}"
    payload["episode_type"] = "main"
    payload["status"] = payload.get("status") or "generated"
    payload["language"] = "es"
    payload["narrative_tone"] = payload.get("narrative_tone") or "epico_historico_oscuro"
    payload["timeline_event_ids"] = story["source_event_ids"]
    payload["parent_episode_id"] = None
    payload["title"] = trim_text(str(payload.get("title") or story["title"]), 120)
    payload["hook"] = trim_text(str(payload.get("hook") or story["summary"]), 300)
    payload["human_review"] = payload.get("human_review") or {
        "required": True,
        "status": "pending",
        "reviewer": None,
        "notes": "Validar fidelidad historica, inferencias y puesta en escena antes del render final.",
    }
    payload["asset_refs"] = payload.get("asset_refs") or {
        "audio": f"local://audio/{payload['episode_id']}.wav",
        "video": f"local://video/{payload['episode_id']}.mp4",
        "subtitles": f"local://subtitles/{payload['episode_id']}.srt",
    }
    payload["created_at"] = payload.get("created_at") or now_iso()
    payload["characters"] = payload.get("characters") or list(story.get("characters", []))
    scenes = payload.get("scenes") or []
    for idx, scene in enumerate(scenes, start=1):
        scene["scene_index"] = int(scene.get("scene_index", idx))
        scene["estimated_seconds"] = int(scene.get("estimated_seconds", scene.get("timing", {}).get("target_duration_seconds", 8)))
    payload["duration_seconds"] = sum(int(scene.get("estimated_seconds", 0)) for scene in scenes)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-pack", default=str(DEFAULT_SOURCE_PACK), help="Approved source_pack.json path")
    parser.add_argument("--character-bible", default=str(DEFAULT_CHARACTER_BIBLE), help="character_bible.json path")
    parser.add_argument("--story-catalog", default=str(DEFAULT_STORY_CATALOG), help="story_catalog.json path")
    parser.add_argument("--story-id", required=True, help="Selected story_id")
    parser.add_argument("--output", default=None, help="Output episode path")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Default output directory when --output is omitted")
    parser.add_argument("--dotenv", default=str(ROOT / ".env"), help="Path to dotenv file")
    parser.add_argument("--model", default=None, help=f"OpenAI model for episode generation (env: {DEFAULT_MODEL_ENV})")
    parser.add_argument("--reasoning-effort", default="medium", help="OpenAI reasoning effort")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting outputs")
    args = parser.parse_args()

    source_pack_path = Path(args.source_pack)
    character_bible_path = Path(args.character_bible)
    story_catalog_path = Path(args.story_catalog)

    for path in [source_pack_path, character_bible_path, story_catalog_path]:
        if not path.exists():
            print(f"ERROR: required file not found: {path}")
            return 1

    try:
        source_pack = read_json(source_pack_path)
        character_bible = read_json(character_bible_path)
        story_catalog = read_json(story_catalog_path)
        ensure_reviewed(source_pack)
        story = find_story(story_catalog, args.story_id)
        load_dotenv_if_present(Path(args.dotenv))
        model = args.model or os.getenv(DEFAULT_MODEL_ENV) or os.getenv("OPENAI_STORY_MODEL") or "gpt-5.4"
        schema = load_schema("episode.schema.json")
        prompt_body = prompt_payload(source_pack, character_bible, story_catalog, story)
        client = build_openai_client(Path(args.dotenv))
        payload = run_structured_generation(
            client=client,
            model=model,
            schema_name="episode",
            schema=schema,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt_body,
            reasoning_effort=args.reasoning_effort,
        )
        payload = normalize_episode(payload, story)
        payload["generation_meta"] = default_generation_meta(
            model=model,
            prompt_version=PROMPT_VERSION,
            prompt_body=SYSTEM_PROMPT + "\n" + prompt_body,
            input_checksum=sha256_text(normalize_ws(prompt_body)),
        )
        output_path = Path(args.output) if args.output else Path(args.output_dir) / f"{payload['episode_id']}.json"
        if output_path.exists() and not args.overwrite:
            raise RuntimeError(f"Output already exists: {output_path}. Use --overwrite.")
        write_json(output_path, payload)
        print(f"Generated episode: {output_path}")
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
