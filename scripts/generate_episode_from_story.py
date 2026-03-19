#!/usr/bin/env python3
"""Generate a final dramatic episode JSON from a selected story."""

from __future__ import annotations

import argparse
import json
import os
import re
from copy import deepcopy
from pathlib import Path

from pipeline_common import (
    ROOT,
    SMART_PUNCT_TRANSLATION,
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
PROOFREAD_PROMPT_VERSION = "episode-proofread-v1"
DEFAULT_SOURCE_PACK = ROOT / "data" / "source" / "source_pack.json"
DEFAULT_CHARACTER_BIBLE = ROOT / "data" / "characters" / "character_bible.json"
DEFAULT_STORY_CATALOG = ROOT / "data" / "story" / "story_catalog.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "episodes" / "generated" / "story-catalog"
DEFAULT_MODEL_ENV = "OPENAI_EPISODE_MODEL"
DEFAULT_REVIEW_MODEL_ENV = "OPENAI_EPISODE_REVIEW_MODEL"
DEFAULT_REVIEW_REASONING_ENV = "OPENAI_EPISODE_REVIEW_REASONING_EFFORT"
DEFAULT_REVIEW_SCOPE_ENV = "OPENAI_EPISODE_REVIEW_SCOPE"

SYSTEM_PROMPT = """
Eres un guionista narrativo-historico para video vertical.
Reglas:
- Responde solo con JSON valido que cumpla el contrato de episode.
- Construye escenas visuales y dramaticas sin contradecir la fuente.
- Marca toda interioridad, subtexto o inferencia en inference_flags y source_evidence.
- Mantén continuidad emocional y de arco de personajes.
- Escribe narracion y dialogo listos para render, en espanol, sin anacronismos.
""".strip()

PROOFREAD_SYSTEM_PROMPT = """
Eres un revisor lingüístico y de estilo para guiones históricos en español.
Tu trabajo es revisar un episode.json ya generado sin alterar su estructura narrativa ni sus hechos.
Reglas:
- Corrige ortografía, tildes, puntuación, concordancia, sintaxis y frases torpes o incoherentes.
- Mantén intactos los hechos históricos, la intención dramática y el número de elementos.
- No cambies IDs, timings, enums, listas, ni referencias de trazabilidad.
- No inventes datos nuevos ni borres información relevante.
- Devuelve solo JSON válido que respete exactamente la estructura solicitada.
""".strip()

TERMINAL_PUNCT_RE = re.compile(r"[.!?…][\"')\]]?$")


def print_llm_warning() -> None:
    print(
        "WARNING: generate_episode_from_story.py uses OpenAI structured generation and will call the API.\n"
        "No built-in --mock is available for this narrative step.\n"
        "If you only need to avoid OpenAI calls during render, use --mock in run_story_pipeline_from_source.sh or run_final_ai_video_pipeline.sh.",
        file=os.sys.stderr,
    )


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


def dedupe_strings(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = normalize_ws(str(raw))
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def capitalize_sentence_starts(text: str) -> str:
    out: list[str] = []
    capitalize_next = True
    prev_chars = ""
    for char in text:
        if capitalize_next and char.isalpha():
            out.append(char.upper())
            capitalize_next = False
        else:
            out.append(char)
            if char.isalpha():
                capitalize_next = False
        prev_chars = (prev_chars + char)[-3:]
        if char in ".!?":
            capitalize_next = True
        elif char == "…" or prev_chars == "...":
            capitalize_next = True
    return "".join(out)


def normalize_fragment_text(text: str) -> str:
    clean = normalize_ws(str(text or "").translate(SMART_PUNCT_TRANSLATION))
    if not clean:
        return ""
    clean = re.sub(r"\s+([,.;:!?])", r"\1", clean)
    clean = re.sub(r"([,.;:!?])(?![\s\"')\]])", r"\1 ", clean)
    clean = re.sub(r"\s*\.\s*\.\s*\.", "...", clean)
    clean = re.sub(r"^\.\.\.\s+(\S)", r"...\1", clean)
    return re.sub(r"\s+", " ", clean).strip()


def normalize_spanish_text(text: str) -> str:
    clean = normalize_fragment_text(text)
    return capitalize_sentence_starts(clean)


def normalize_optional_spanish_text(value: object) -> str | None:
    if value is None:
        return None
    clean = normalize_spanish_text(str(value))
    return clean or None


def normalize_reviewable_text_fields(payload: dict) -> dict:
    payload["title"] = normalize_fragment_text(str(payload.get("title") or ""))
    payload["hook"] = normalize_spanish_text(str(payload.get("hook") or ""))

    storytelling = payload.get("storytelling") or {}
    for field in ("premise", "dramatic_question", "ending_payoff"):
        if field in storytelling:
            storytelling[field] = normalize_spanish_text(str(storytelling.get(field) or ""))

    plot_twist = payload.get("plot_twist") or {}
    for field in ("setup", "reveal", "payoff"):
        if field in plot_twist:
            plot_twist[field] = normalize_spanish_text(str(plot_twist.get(field) or ""))

    if "prompt_rationale" in payload:
        payload["prompt_rationale"] = normalize_optional_spanish_text(payload.get("prompt_rationale"))

    human_review = payload.get("human_review") or {}
    if "notes" in human_review:
        human_review["notes"] = normalize_optional_spanish_text(human_review.get("notes"))

    for beat in payload.get("character_beats") or []:
        if "beat_summary" in beat:
            beat["beat_summary"] = normalize_spanish_text(str(beat.get("beat_summary") or ""))

    for evidence in payload.get("source_evidence") or []:
        if "summary" in evidence:
            evidence["summary"] = normalize_spanish_text(str(evidence.get("summary") or ""))

    for scene in payload.get("scenes") or []:
        for field in (
            "source_beat",
            "visual_focus",
            "transition_note",
            "narration",
            "visual_prompt",
            "scene_objective",
            "subtext_or_private_state_summary",
            "prompt_rationale",
        ):
            if field in scene:
                value = scene.get(field)
                if field in {"scene_objective", "subtext_or_private_state_summary", "prompt_rationale"}:
                    scene[field] = normalize_optional_spanish_text(value)
                else:
                    scene[field] = normalize_spanish_text(str(value or ""))
        cleaned_notes: list[str] = []
        for note in scene.get("behavioral_notes") or []:
            cleaned_note = normalize_spanish_text(str(note))
            if cleaned_note:
                cleaned_notes.append(cleaned_note)
        scene["behavioral_notes"] = cleaned_notes
        for dialogue in scene.get("dialogue") or []:
            if "speaker" in dialogue:
                dialogue["speaker"] = normalize_fragment_text(str(dialogue.get("speaker") or ""))
            if "line" in dialogue:
                dialogue["line"] = normalize_spanish_text(str(dialogue.get("line") or ""))
        for evidence in scene.get("source_evidence") or []:
            if "summary" in evidence:
                evidence["summary"] = normalize_spanish_text(str(evidence.get("summary") or ""))
        for snapshot_group in ("character_state_before", "character_state_after"):
            for snapshot in scene.get(snapshot_group) or []:
                if "summary" in snapshot:
                    snapshot["summary"] = normalize_spanish_text(str(snapshot.get("summary") or ""))
    return payload


def trim_optional_text(value: object, max_len: int) -> str | None:
    if value is None:
        return None
    clean = trim_text(str(value), max_len)
    return clean or None


def enforce_reviewable_text_limits(payload: dict) -> dict:
    payload["title"] = trim_text(str(payload.get("title") or ""), 120)
    payload["hook"] = trim_text(str(payload.get("hook") or ""), 300)

    storytelling = payload.get("storytelling") or {}
    storytelling["premise"] = trim_text(str(storytelling.get("premise") or ""), 260)
    storytelling["dramatic_question"] = trim_text(str(storytelling.get("dramatic_question") or ""), 260)
    storytelling["ending_payoff"] = trim_text(str(storytelling.get("ending_payoff") or ""), 260)

    plot_twist = payload.get("plot_twist") or {}
    plot_twist["setup"] = trim_text(str(plot_twist.get("setup") or ""), 400)
    plot_twist["reveal"] = trim_text(str(plot_twist.get("reveal") or ""), 400)
    plot_twist["payoff"] = trim_text(str(plot_twist.get("payoff") or ""), 400)

    payload["prompt_rationale"] = trim_optional_text(payload.get("prompt_rationale"), 320)
    human_review = payload.get("human_review") or {}
    human_review["notes"] = trim_optional_text(human_review.get("notes"), 1500)

    for beat in payload.get("character_beats") or []:
        beat["beat_summary"] = trim_text(str(beat.get("beat_summary") or ""), 300)

    for evidence in payload.get("source_evidence") or []:
        evidence["summary"] = trim_text(str(evidence.get("summary") or ""), 320)

    for scene in payload.get("scenes") or []:
        scene["source_beat"] = trim_text(str(scene.get("source_beat") or ""), 220)
        scene["visual_focus"] = trim_text(str(scene.get("visual_focus") or ""), 280)
        scene["transition_note"] = trim_text(str(scene.get("transition_note") or ""), 220)
        scene["narration"] = trim_text(str(scene.get("narration") or ""), 900)
        scene["visual_prompt"] = trim_text(str(scene.get("visual_prompt") or ""), 900)
        scene["scene_objective"] = trim_optional_text(scene.get("scene_objective"), 240)
        scene["subtext_or_private_state_summary"] = trim_optional_text(scene.get("subtext_or_private_state_summary"), 320)
        scene["prompt_rationale"] = trim_optional_text(scene.get("prompt_rationale"), 320)
        scene["behavioral_notes"] = [trim_text(str(note), 220) for note in (scene.get("behavioral_notes") or [])]

        for dialogue in scene.get("dialogue") or []:
            dialogue["speaker"] = trim_text(str(dialogue.get("speaker") or ""), 80)
            dialogue["line"] = trim_text(str(dialogue.get("line") or ""), 180)

        for evidence in scene.get("source_evidence") or []:
            evidence["summary"] = trim_text(str(evidence.get("summary") or ""), 320)

        for snapshot_group in ("character_state_before", "character_state_after"):
            for snapshot in scene.get(snapshot_group) or []:
                snapshot["summary"] = trim_text(str(snapshot.get("summary") or ""), 220)
    return payload


def normalize_review_scope(value: str | None) -> str:
    scope = normalize_ws(str(value or "")).lower()
    if scope in {"", "full"}:
        return "full"
    if scope in {"render", "render_only"}:
        return "render"
    raise RuntimeError(f"Unsupported review scope: {value!r}. Use 'full' or 'render'.")


def build_proofread_payload(payload: dict, *, scope: str = "full") -> dict:
    if scope == "render":
        return {
            "title": payload.get("title"),
            "scenes": [
                {
                    "narration": scene.get("narration"),
                    "dialogue": [
                        {
                            "speaker": line.get("speaker"),
                            "line": line.get("line"),
                        }
                        for line in (scene.get("dialogue") or [])
                    ],
                }
                for scene in (payload.get("scenes") or [])
            ],
        }
    return {
        "title": payload.get("title"),
        "hook": payload.get("hook"),
        "storytelling": {
            "premise": ((payload.get("storytelling") or {}).get("premise")),
            "dramatic_question": ((payload.get("storytelling") or {}).get("dramatic_question")),
            "ending_payoff": ((payload.get("storytelling") or {}).get("ending_payoff")),
        },
        "plot_twist": {
            "setup": ((payload.get("plot_twist") or {}).get("setup")),
            "reveal": ((payload.get("plot_twist") or {}).get("reveal")),
            "payoff": ((payload.get("plot_twist") or {}).get("payoff")),
        },
        "character_beats": [
            {
                "beat_summary": beat.get("beat_summary"),
            }
            for beat in (payload.get("character_beats") or [])
        ],
        "source_evidence": [
            {
                "summary": evidence.get("summary"),
            }
            for evidence in (payload.get("source_evidence") or [])
        ],
        "prompt_rationale": payload.get("prompt_rationale"),
        "human_review": {
            "notes": ((payload.get("human_review") or {}).get("notes")),
        },
        "scenes": [
            {
                "source_beat": scene.get("source_beat"),
                "visual_focus": scene.get("visual_focus"),
                "transition_note": scene.get("transition_note"),
                "narration": scene.get("narration"),
                "visual_prompt": scene.get("visual_prompt"),
                "dialogue": [
                    {
                        "speaker": line.get("speaker"),
                        "line": line.get("line"),
                    }
                    for line in (scene.get("dialogue") or [])
                ],
                "scene_objective": scene.get("scene_objective"),
                "behavioral_notes": list(scene.get("behavioral_notes") or []),
                "source_evidence": [
                    {
                        "summary": evidence.get("summary"),
                    }
                    for evidence in (scene.get("source_evidence") or [])
                ],
                "character_state_before": [
                    {
                        "summary": snapshot.get("summary"),
                    }
                    for snapshot in (scene.get("character_state_before") or [])
                ],
                "character_state_after": [
                    {
                        "summary": snapshot.get("summary"),
                    }
                    for snapshot in (scene.get("character_state_after") or [])
                ],
                "subtext_or_private_state_summary": scene.get("subtext_or_private_state_summary"),
                "prompt_rationale": scene.get("prompt_rationale"),
            }
            for scene in (payload.get("scenes") or [])
        ],
    }


def build_text_review_schema(value: object) -> dict:
    if value is None:
        return {"type": "null"}
    if isinstance(value, str):
        return {
            "type": "string",
            "maxLength": max(64, len(value) + 160),
        }
    if isinstance(value, list):
        item_schemas = [build_text_review_schema(item) for item in value]
        if not item_schemas:
            return {"type": "array", "minItems": 0, "maxItems": 0, "items": {"type": "string"}}
        return {
            "type": "array",
            "minItems": len(value),
            "maxItems": len(value),
            "items": merge_text_review_item_schemas(item_schemas),
        }
    if isinstance(value, dict):
        properties = {key: build_text_review_schema(item) for key, item in value.items()}
        return {
            "type": "object",
            "additionalProperties": False,
            "required": list(value.keys()),
            "properties": properties,
        }
    raise RuntimeError(f"Unsupported text review payload value: {type(value)!r}")


def merge_text_review_item_schemas(item_schemas: list[dict]) -> dict:
    first_schema = item_schemas[0]
    first_type = first_schema.get("type")
    if any(schema.get("type") != first_type for schema in item_schemas[1:]):
        raise RuntimeError("Text review payload contains heterogeneous arrays.")

    if first_type == "null":
        return {"type": "null"}

    if first_type == "string":
        return {
            "type": "string",
            "maxLength": max(int(schema.get("maxLength") or 64) for schema in item_schemas),
        }

    if first_type == "array":
        merged_items = [schema.get("items") for schema in item_schemas if schema.get("items") is not None]
        if not merged_items:
            merged_item_schema = {"type": "string"}
        else:
            merged_item_schema = merge_text_review_item_schemas(merged_items)
        return {
            "type": "array",
            "minItems": min(int(schema.get("minItems") or 0) for schema in item_schemas),
            "maxItems": max(int(schema.get("maxItems") or 0) for schema in item_schemas),
            "items": merged_item_schema,
        }

    if first_type == "object":
        first_keys = list(first_schema.get("properties", {}).keys())
        for schema in item_schemas[1:]:
            if list(schema.get("properties", {}).keys()) != first_keys:
                raise RuntimeError("Text review payload contains heterogeneous arrays.")
        return {
            "type": "object",
            "additionalProperties": False,
            "required": first_keys,
            "properties": {
                key: merge_text_review_item_schemas(
                    [schema["properties"][key] for schema in item_schemas]
                )
                for key in first_keys
            },
        }

    raise RuntimeError("Text review payload contains heterogeneous arrays.")


def merge_proofread_payload(payload: dict, reviewed: dict, *, scope: str = "full") -> dict:
    if scope == "render":
        payload["title"] = reviewed["title"]
        for scene, reviewed_scene in zip(payload.get("scenes") or [], reviewed.get("scenes") or []):
            scene["narration"] = reviewed_scene["narration"]
            for dialogue, reviewed_dialogue in zip(scene.get("dialogue") or [], reviewed_scene.get("dialogue") or []):
                dialogue["speaker"] = reviewed_dialogue["speaker"]
                dialogue["line"] = reviewed_dialogue["line"]
        return payload

    payload["title"] = reviewed["title"]
    payload["hook"] = reviewed["hook"]
    payload["storytelling"]["premise"] = reviewed["storytelling"]["premise"]
    payload["storytelling"]["dramatic_question"] = reviewed["storytelling"]["dramatic_question"]
    payload["storytelling"]["ending_payoff"] = reviewed["storytelling"]["ending_payoff"]
    payload["plot_twist"]["setup"] = reviewed["plot_twist"]["setup"]
    payload["plot_twist"]["reveal"] = reviewed["plot_twist"]["reveal"]
    payload["plot_twist"]["payoff"] = reviewed["plot_twist"]["payoff"]
    payload["prompt_rationale"] = reviewed["prompt_rationale"]
    payload["human_review"]["notes"] = reviewed["human_review"]["notes"]

    for beat, reviewed_beat in zip(payload.get("character_beats") or [], reviewed.get("character_beats") or []):
        beat["beat_summary"] = reviewed_beat["beat_summary"]

    for evidence, reviewed_evidence in zip(payload.get("source_evidence") or [], reviewed.get("source_evidence") or []):
        evidence["summary"] = reviewed_evidence["summary"]

    for scene, reviewed_scene in zip(payload.get("scenes") or [], reviewed.get("scenes") or []):
        for field in (
            "source_beat",
            "visual_focus",
            "transition_note",
            "narration",
            "visual_prompt",
            "scene_objective",
            "subtext_or_private_state_summary",
            "prompt_rationale",
        ):
            scene[field] = reviewed_scene[field]
        scene["behavioral_notes"] = list(reviewed_scene.get("behavioral_notes") or [])

        for dialogue, reviewed_dialogue in zip(scene.get("dialogue") or [], reviewed_scene.get("dialogue") or []):
            dialogue["speaker"] = reviewed_dialogue["speaker"]
            dialogue["line"] = reviewed_dialogue["line"]

        for evidence, reviewed_evidence in zip(scene.get("source_evidence") or [], reviewed_scene.get("source_evidence") or []):
            evidence["summary"] = reviewed_evidence["summary"]

        for snapshot, reviewed_snapshot in zip(scene.get("character_state_before") or [], reviewed_scene.get("character_state_before") or []):
            snapshot["summary"] = reviewed_snapshot["summary"]

        for snapshot, reviewed_snapshot in zip(scene.get("character_state_after") or [], reviewed_scene.get("character_state_after") or []):
            snapshot["summary"] = reviewed_snapshot["summary"]
    return payload


def text_has_terminal_punctuation(text: str) -> bool:
    clean = normalize_fragment_text(text)
    return bool(clean) and bool(TERMINAL_PUNCT_RE.search(clean))


def starts_with_uppercase(text: str) -> bool:
    for char in normalize_fragment_text(text):
        if char.isalpha():
            return char.isupper()
    return True


def collect_text_quality_issues(payload: dict, *, scope: str = "full") -> list[str]:
    issues: list[str] = []

    def check(path: str, text: str | None, *, terminal_punctuation: bool) -> None:
        if text is None:
            return
        raw = str(text)
        clean = normalize_fragment_text(raw)
        if not clean:
            issues.append(f"{path}: texto vacio tras normalizacion")
            return
        if re.search(r"\s{2,}", raw):
            issues.append(f"{path}: contiene espacios duplicados")
        if re.search(r"\s+([,.;:!?])", raw):
            issues.append(f"{path}: contiene espacios antes de signos de puntuacion")
        if terminal_punctuation and not text_has_terminal_punctuation(clean):
            issues.append(f"{path}: falta puntuacion final")
        if terminal_punctuation and not starts_with_uppercase(clean):
            issues.append(f"{path}: no empieza con mayuscula")
        if "?" in clean and "¿" not in clean:
            issues.append(f"{path}: pregunta sin apertura de interrogacion")
        if "!" in clean and "¡" not in clean:
            issues.append(f"{path}: exclamacion sin apertura")

    check("title", payload.get("title"), terminal_punctuation=False)
    if scope == "render":
        for scene_idx, scene in enumerate(payload.get("scenes") or [], start=1):
            check(f"scenes[{scene_idx}].narration", scene.get("narration"), terminal_punctuation=True)
            for line_idx, line in enumerate(scene.get("dialogue") or [], start=1):
                check(f"scenes[{scene_idx}].dialogue[{line_idx}].speaker", line.get("speaker"), terminal_punctuation=False)
                check(f"scenes[{scene_idx}].dialogue[{line_idx}].line", line.get("line"), terminal_punctuation=True)
        deduped: list[str] = []
        seen: set[str] = set()
        for issue in issues:
            if issue in seen:
                continue
            seen.add(issue)
            deduped.append(issue)
        return deduped

    check("hook", payload.get("hook"), terminal_punctuation=True)

    storytelling = payload.get("storytelling") or {}
    for field in ("premise", "dramatic_question", "ending_payoff"):
        check(f"storytelling.{field}", storytelling.get(field), terminal_punctuation=True)

    plot_twist = payload.get("plot_twist") or {}
    for field in ("setup", "reveal", "payoff"):
        check(f"plot_twist.{field}", plot_twist.get(field), terminal_punctuation=True)

    check("prompt_rationale", payload.get("prompt_rationale"), terminal_punctuation=True)
    check("human_review.notes", ((payload.get("human_review") or {}).get("notes")), terminal_punctuation=True)

    for idx, beat in enumerate(payload.get("character_beats") or [], start=1):
        check(f"character_beats[{idx}].beat_summary", beat.get("beat_summary"), terminal_punctuation=True)

    for idx, evidence in enumerate(payload.get("source_evidence") or [], start=1):
        check(f"source_evidence[{idx}].summary", evidence.get("summary"), terminal_punctuation=True)

    for scene_idx, scene in enumerate(payload.get("scenes") or [], start=1):
        for field in (
            "source_beat",
            "visual_focus",
            "transition_note",
            "narration",
            "visual_prompt",
            "scene_objective",
            "subtext_or_private_state_summary",
            "prompt_rationale",
        ):
            check(f"scenes[{scene_idx}].{field}", scene.get(field), terminal_punctuation=field != "visual_focus")
        for note_idx, note in enumerate(scene.get("behavioral_notes") or [], start=1):
            check(f"scenes[{scene_idx}].behavioral_notes[{note_idx}]", note, terminal_punctuation=True)
        for line_idx, line in enumerate(scene.get("dialogue") or [], start=1):
            check(f"scenes[{scene_idx}].dialogue[{line_idx}].speaker", line.get("speaker"), terminal_punctuation=False)
            check(f"scenes[{scene_idx}].dialogue[{line_idx}].line", line.get("line"), terminal_punctuation=True)
        for evidence_idx, evidence in enumerate(scene.get("source_evidence") or [], start=1):
            check(f"scenes[{scene_idx}].source_evidence[{evidence_idx}].summary", evidence.get("summary"), terminal_punctuation=True)
        for snapshot_group in ("character_state_before", "character_state_after"):
            for snapshot_idx, snapshot in enumerate(scene.get(snapshot_group) or [], start=1):
                check(f"scenes[{scene_idx}].{snapshot_group}[{snapshot_idx}].summary", snapshot.get("summary"), terminal_punctuation=True)

    deduped: list[str] = []
    seen: set[str] = set()
    for issue in issues:
        if issue in seen:
            continue
        seen.add(issue)
        deduped.append(issue)
    return deduped


def proofread_episode_text(
    *,
    client: object,
    model: str,
    payload: dict,
    reasoning_effort: str,
    scope: str = "full",
    max_rounds: int = 2,
) -> dict:
    reviewed_payload = deepcopy(payload)
    feedback: list[str] = []

    for round_idx in range(1, max_rounds + 1):
        review_payload = build_proofread_payload(reviewed_payload, scope=scope)
        user_prompt = {
            "task": "Revisa el texto del episodio y devuelve exactamente la misma estructura con el texto corregido.",
            "review_scope": scope,
            "locked_constraints": [
                "No cambies el numero de escenas, dialogos, evidencias ni snapshots.",
                "No alteres hechos, nombres propios, relaciones causales ni trazabilidad.",
                "No modifiques intencion dramatica: solo calidad linguistica y claridad.",
            ],
            "feedback_from_previous_round": feedback,
            "episode_text": review_payload,
        }
        reviewed_text = run_structured_generation(
            client=client,
            model=model,
            schema_name="episode_text_review",
            schema=build_text_review_schema(review_payload),
            system_prompt=PROOFREAD_SYSTEM_PROMPT,
            user_prompt=json.dumps(user_prompt, ensure_ascii=False, indent=2),
            reasoning_effort=reasoning_effort,
        )
        merge_proofread_payload(reviewed_payload, reviewed_text, scope=scope)
        normalize_reviewable_text_fields(reviewed_payload)
        enforce_reviewable_text_limits(reviewed_payload)
        feedback = collect_text_quality_issues(reviewed_payload, scope=scope)
        if not feedback:
            return reviewed_payload

    summary = "\n".join(f"- {issue}" for issue in feedback[:12])
    if len(feedback) > 12:
        summary += f"\n- ... y {len(feedback) - 12} incidencias mas"
    raise RuntimeError(f"Text review gate failed after {max_rounds} rounds:\n{summary}")


def strip_null_optional_fields(scene: dict) -> None:
    for field in (
        "dialogue",
        "scene_objective",
        "source_evidence",
        "character_state_before",
        "character_state_after",
        "behavioral_notes",
        "subtext_or_private_state_summary",
        "prompt_rationale",
        "inference_flags",
    ):
        if scene.get(field) is None:
            scene.pop(field, None)


def normalize_episode(payload: dict, story: dict, source_pack: dict) -> dict:
    story_slug = str(story["story_id"]).replace("story-", "")[:48]
    payload["episode_id"] = payload.get("episode_id") or f"main-{now_iso()[:10].replace('-', '')}-{story_slug}"
    payload["episode_type"] = "main"
    payload["status"] = payload.get("status") or "generated"
    payload["language"] = "es"
    payload["narrative_tone"] = payload.get("narrative_tone") or "epico_historico_oscuro"
    valid_event_ids = {str(event.get("event_id")) for event in source_pack.get("derived_events", []) if event.get("event_id")}
    timeline_event_ids = [event_id for event_id in story.get("source_event_ids", []) if event_id in valid_event_ids]
    payload["timeline_event_ids"] = timeline_event_ids or list(story.get("source_event_ids", []))
    payload["parent_episode_id"] = None
    payload["title"] = trim_text(str(payload.get("title") or story["title"]), 120)
    payload["hook"] = trim_text(str(payload.get("hook") or story["summary"]), 300)
    human_review = dict(payload.get("human_review") or {})
    payload["human_review"] = {
        "required": True,
        "status": human_review.get("status") or "pending",
        "reviewer": human_review.get("reviewer"),
        "notes": trim_text(
            str(
                human_review.get("notes")
                or "Validar fidelidad historica, inferencias y puesta en escena antes del render final."
            ),
            1500,
        ),
    }
    asset_refs = dict(payload.get("asset_refs") or {})
    payload["asset_refs"] = {
        "audio": str(asset_refs.get("audio") or f"local://audio/{payload['episode_id']}.wav"),
        "video": str(asset_refs.get("video") or f"local://video/{payload['episode_id']}.mp4"),
        "subtitles": str(asset_refs.get("subtitles") or f"local://subtitles/{payload['episode_id']}.srt"),
    }
    payload["created_at"] = payload.get("created_at") or now_iso()
    base_characters = dedupe_strings(list(payload.get("characters") or story.get("characters", [])))
    scenes = payload.get("scenes") or []
    extra_cast: list[str] = []
    for idx, scene in enumerate(scenes, start=1):
        scene["scene_index"] = int(scene.get("scene_index", idx))
        scene["estimated_seconds"] = int(scene.get("estimated_seconds", scene.get("timing", {}).get("target_duration_seconds", 8)))
        scene["scene_cast"] = dedupe_strings(list(scene.get("scene_cast") or []))
        extra_cast.extend(scene["scene_cast"])
        strip_null_optional_fields(scene)
    payload["characters"] = dedupe_strings(base_characters + extra_cast)
    payload["duration_seconds"] = sum(int(scene.get("estimated_seconds", 0)) for scene in scenes)
    return payload


def build_generation_schema(base_schema: dict) -> dict:
    schema = deepcopy(base_schema)
    schema.pop("allOf", None)

    properties = schema.get("properties") or {}
    episode_type_schema = dict(properties.get("episode_type") or {})
    episode_type_schema.pop("enum", None)
    episode_type_schema["const"] = "main"
    properties["episode_type"] = episode_type_schema

    parent_episode_schema = dict(properties.get("parent_episode_id") or {})
    parent_episode_schema["type"] = "null"
    parent_episode_schema.pop("pattern", None)
    properties["parent_episode_id"] = parent_episode_schema

    duration_schema = dict(properties.get("duration_seconds") or {})
    duration_schema["type"] = "integer"
    duration_schema["minimum"] = 90
    duration_schema["maximum"] = 180
    properties["duration_seconds"] = duration_schema
    schema["properties"] = properties

    required = list(schema.get("required") or [])
    for field in ("plot_twist", "character_beats"):
        if field not in required:
            required.append(field)
    schema["required"] = required
    return schema


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
        review_model = os.getenv(DEFAULT_REVIEW_MODEL_ENV) or model
        review_reasoning_effort = os.getenv(DEFAULT_REVIEW_REASONING_ENV) or "low"
        review_scope = normalize_review_scope(os.getenv(DEFAULT_REVIEW_SCOPE_ENV) or "full")
        schema = build_generation_schema(load_schema("episode.schema.json"))
        prompt_body = prompt_payload(source_pack, character_bible, story_catalog, story)
        print_llm_warning()
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
        payload = normalize_episode(payload, story, source_pack)
        normalize_reviewable_text_fields(payload)
        enforce_reviewable_text_limits(payload)
        review_seed_payload = build_proofread_payload(payload, scope=review_scope)
        payload = proofread_episode_text(
            client=client,
            model=review_model,
            payload=payload,
            reasoning_effort=review_reasoning_effort,
            scope=review_scope,
        )
        normalize_reviewable_text_fields(payload)
        enforce_reviewable_text_limits(payload)
        final_issues = collect_text_quality_issues(payload, scope=review_scope)
        if final_issues:
            raise RuntimeError("Text review gate failed after final normalization:\n- " + "\n- ".join(final_issues[:12]))
        payload["generation_meta"] = default_generation_meta(
            model=(model if review_model == model else f"{model}+{review_model}")[:80],
            prompt_version=f"{PROMPT_VERSION}+{PROOFREAD_PROMPT_VERSION}",
            prompt_body=SYSTEM_PROMPT + "\n" + prompt_body + "\n" + PROOFREAD_SYSTEM_PROMPT,
            input_checksum=sha256_text(
                normalize_ws(prompt_body)
                + "\n"
                + normalize_ws(json.dumps(review_seed_payload, ensure_ascii=False))
            ),
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
