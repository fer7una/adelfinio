#!/usr/bin/env python3
"""Generate per-scene images and per-phase voice audio from an episode JSON.

Modes:
- Real mode (default): uses OpenAI APIs (image + TTS)
- Mock mode (--mock): generates placeholder image/audio with ffmpeg
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

try:
    from layout_analysis import analyze_and_assign_layout, assign_scene_layout
    from video_text_layout import fit_wrapped_text, overlay_text_config, text_fits_overlay
except ModuleNotFoundError:  # Support package-style imports in local tooling.
    from scripts.layout_analysis import analyze_and_assign_layout, assign_scene_layout
    from scripts.video_text_layout import fit_wrapped_text, overlay_text_config, text_fits_overlay

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ASSETS_DIR = ROOT / "artifacts" / "scene_assets"
DEFAULT_CHAR_DIR = ROOT / "data" / "characters"
MOCK_SCENE_COLORS = [
    "#0f172a",
    "#1d4ed8",
    "#b45309",
    "#065f46",
    "#7c2d12",
    "#4c1d95",
]
SUPPORTED_OPENAI_TTS_VOICES = {
    "alloy",
    "echo",
    "fable",
    "onyx",
    "nova",
    "shimmer",
    "coral",
    "verse",
    "ballad",
    "ash",
    "sage",
    "marin",
    "cedar",
}
SEMANTIC_TTS_VOICE_MAP = {
    "tenor_bajo_hieratico": "onyx",
    "baritono_rudo": "ash",
    "soprano_madura": "shimmer",
}
SMART_PUNCT_TRANSLATION = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "´": "'",
        "`": "'",
        "–": "-",
        "—": "-",
        "…": "...",
        "«": '"',
        "»": '"',
        "\u00a0": " ",
    }
)


def default_narrator_profile() -> dict:
    try:
        narrator_wps = float(os.getenv("VIDEO_NARRATOR_WPS", "2.55"))
    except ValueError:
        narrator_wps = 2.55
    default_voice = normalize_tts_voice_id(os.getenv("VIDEO_NARRATOR_TTS_VOICE", os.getenv("OPENAI_TTS_VOICE", "alloy")))
    return {
        "tts_voice": default_voice,
        "tone": os.getenv("VIDEO_NARRATOR_TONE", "cronista_epico"),
        "words_per_second": narrator_wps,
        "delivery_modifiers": {
            "normal": 1.0,
            "shout": 1.12,
        },
    }


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid episode payload: {path}")
    return payload


def dump_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def load_dotenv_if_present(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_tts_voice_id(value: str | None, fallback: str = "alloy") -> str:
    clean = normalize_ws(value).lower().replace(" ", "_")
    fallback_clean = normalize_ws(fallback).lower().replace(" ", "_") or "alloy"
    if clean in SUPPORTED_OPENAI_TTS_VOICES:
        return clean
    mapped = SEMANTIC_TTS_VOICE_MAP.get(clean)
    if mapped:
        return mapped
    if any(token in clean for token in ("soprano", "mujer", "femen", "materna")):
        return "shimmer"
    if any(token in clean for token in ("tenor", "baritono", "grave", "hieratico", "solemne")):
        return "onyx"
    if any(token in clean for token in ("rudo", "aspero", "guerrero", "bronco")):
        return "ash"
    if fallback_clean in SUPPORTED_OPENAI_TTS_VOICES:
        return fallback_clean
    return "alloy"


def normalized_voice_profile(voice_profile: dict | None) -> dict:
    narrator_profile = default_narrator_profile()
    profile = dict(voice_profile or {})
    delivery_modifiers = profile.get("delivery_modifiers")
    profile["tts_voice"] = normalize_tts_voice_id(str(profile.get("tts_voice", narrator_profile["tts_voice"])), narrator_profile["tts_voice"])
    profile["tone"] = normalize_ws(str(profile.get("tone", narrator_profile["tone"]))) or narrator_profile["tone"]
    try:
        profile["words_per_second"] = float(profile.get("words_per_second", narrator_profile["words_per_second"]))
    except (TypeError, ValueError):
        profile["words_per_second"] = narrator_profile["words_per_second"]
    if not isinstance(delivery_modifiers, dict):
        delivery_modifiers = narrator_profile["delivery_modifiers"]
    profile["delivery_modifiers"] = {
        "normal": float(delivery_modifiers.get("normal", narrator_profile["delivery_modifiers"]["normal"])),
        "shout": float(delivery_modifiers.get("shout", narrator_profile["delivery_modifiers"]["shout"])),
    }
    return profile


def capitalize_sentence_starts(text: str) -> str:
    out: list[str] = []
    capitalize_next = True
    prev_chars = ""
    for char in text:
        if char.isalpha() and capitalize_next:
            out.append(char.upper())
            capitalize_next = False
        else:
            out.append(char)
            if char.isalpha():
                capitalize_next = False
        prev_chars = (prev_chars + char)[-3:]
        if char in ".!?":
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


def display_label(value: str) -> str:
    clean = normalize_ws(str(value or "")).replace("_", " ").replace("-", " ")
    if not clean:
        return ""
    return " ".join(token[:1].upper() + token[1:] for token in clean.split())


def canonical_actor_name(value: str) -> str:
    clean = normalize_ws(str(value or ""))
    clean = re.sub(r"\s*\([^)]*\)", "", clean)
    clean = re.sub(r"\bvoz en off\b", "", clean, flags=re.IGNORECASE)
    return normalize_ws(clean).strip(" ,.;:-")


def trim_caption(text: str, max_len: int = 140) -> str:
    clean = normalize_ws(text)
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3].rstrip(" ,;:") + "..."


def with_mid_ellipsis(text: str) -> str:
    base = normalize_ws(text).strip(". ")
    if not base:
        return "..."
    return f"... {base} ..."


def overlay_page_is_readable(
    text: str,
    config: dict,
    *,
    enforce_preferred: bool,
) -> bool:
    if not text_fits_overlay(text, **config):
        return False
    if not enforce_preferred:
        return True
    wrapped, font_size, _ = fit_wrapped_text(text, **config)
    if "..." in wrapped and "..." not in normalize_fragment_text(text):
        return False
    preferred_min = int(config.get("preferred_min_font_size", config.get("min_font_size", 0)) or 0)
    return font_size >= preferred_min


def pack_overlay_pages(
    words: list[str],
    *,
    config: dict,
) -> list[str]:
    pages: list[str] = []
    current_words: list[str] = []
    for word in words:
        clean_word = normalize_fragment_text(word)
        if not clean_word:
            continue
        candidate_words = current_words + [clean_word]
        candidate = normalize_fragment_text(" ".join(candidate_words))
        if current_words and overlay_page_is_readable(candidate, config, enforce_preferred=False):
            current_words = candidate_words
            continue
        if current_words:
            pages.append(normalize_fragment_text(" ".join(current_words)))
        current_words = [clean_word]
    if current_words:
        pages.append(normalize_fragment_text(" ".join(current_words)))
    return pages


def format_continuation_page(text: str, *, has_prev: bool, has_next: bool) -> str:
    page = normalize_fragment_text(text)
    if not page:
        return ""
    if has_next:
        page = page.rstrip(" ,;:.")
    if has_prev:
        page = f"...{page}"
    if has_next:
        page = f"{page}..."
    formatted = normalize_fragment_text(page)
    if has_prev and formatted.startswith("... "):
        formatted = "..." + formatted[4:]
    return formatted


def decorate_continuation_pages(pages: list[str]) -> list[str]:
    total = len(pages)
    output: list[str] = []
    for idx, page in enumerate(pages):
        display = format_continuation_page(
            page,
            has_prev=idx > 0,
            has_next=idx < total - 1,
        )
        if display:
            output.append(display)
    return output


def split_page_once(text: str) -> list[str]:
    words = normalize_fragment_text(text).split()
    if len(words) <= 1:
        return [normalize_fragment_text(text)]
    midpoint = max(1, len(words) // 2)
    left = normalize_fragment_text(" ".join(words[:midpoint]))
    right = normalize_fragment_text(" ".join(words[midpoint:]))
    return [page for page in (left, right) if page]


def ensure_decorated_pages_fit(raw_pages: list[str], *, config: dict) -> list[str]:
    pages = [normalize_fragment_text(page) for page in raw_pages if normalize_fragment_text(page)]
    if len(pages) <= 1:
        return pages

    while True:
        display_pages = decorate_continuation_pages(pages)
        bad_idx = next(
            (
                idx
                for idx, display_page in enumerate(display_pages)
                if not overlay_page_is_readable(display_page, config, enforce_preferred=False)
            ),
            None,
        )
        if bad_idx is None:
            return pages

        replacement = split_page_once(pages[bad_idx])
        if len(replacement) <= 1:
            return pages
        pages = pages[:bad_idx] + replacement + pages[bad_idx + 1 :]


def split_text_sequentially_to_fit(
    text: str,
    *,
    config: dict,
) -> list[str]:
    clean = normalize_fragment_text(text)
    if not clean:
        return []
    if overlay_page_is_readable(clean, config, enforce_preferred=False):
        return [clean]
    words = clean.split()
    if len(words) <= 1:
        return [clean]
    pages = pack_overlay_pages(words, config=config)
    if not pages:
        return [clean]
    repaired: list[str] = []
    for page in pages:
        if overlay_page_is_readable(page, config, enforce_preferred=False):
            repaired.append(page)
            continue
        midpoint = max(1, len(page.split()) // 2)
        split_words = page.split()
        repaired.extend(
            [
                normalize_fragment_text(" ".join(split_words[:midpoint])),
                normalize_fragment_text(" ".join(split_words[midpoint:])),
            ]
        )
    return [page for page in repaired if page]


def infer_focus_anchor(scene: dict, block_index: int, *, dialogue_speaker: str = "") -> str:
    scene_text = " ".join(
        normalize_ws(str(scene.get(field, "")))
        for field in ("visual_focus", "visual_prompt", "scene_objective")
    ).lower()
    if any(token in scene_text for token in ("izquierda", "a la izquierda", "flanco izquierdo", "lado izquierdo")):
        return "upper_left"
    if any(token in scene_text for token in ("derecha", "a la derecha", "flanco derecho", "lado derecho")):
        return "upper_right"
    actor = canonical_actor_name(dialogue_speaker)
    if actor and actor.lower() != "narrador":
        return "upper_right" if block_index % 2 else "upper_left"
    cast = scene.get("scene_cast")
    if isinstance(cast, list) and len(cast) == 1:
        return "upper_center"
    if isinstance(cast, list) and len(cast) > 1:
        return "upper_left" if block_index % 2 else "upper_right"
    return "upper_center"


def infer_primary_actor(scene: dict, dialogue_speaker: str) -> str:
    actor = canonical_actor_name(dialogue_speaker)
    if actor and actor.lower() != "narrador":
        return actor
    cast = scene.get("scene_cast")
    if isinstance(cast, list):
        for item in cast:
            label = canonical_actor_name(display_label(str(item)))
            if label:
                return label
    return ""


def bubble_anchor_for_focus(focus_anchor: str, block_index: int) -> str:
    if focus_anchor == "upper_left":
        return "upper_right"
    if focus_anchor == "upper_right":
        return "upper_left"
    return "upper_left" if block_index % 2 else "upper_right"


def collect_scene_proper_nouns(scene: dict, scene_dialogue: list[dict]) -> set[str]:
    proper_nouns: set[str] = set()
    cast = scene.get("scene_cast")
    if isinstance(cast, list):
        for item in cast:
            label = canonical_actor_name(display_label(str(item)))
            if label:
                proper_nouns.update(part for part in label.split() if part)
    for row in scene_dialogue:
        label = canonical_actor_name(str(row.get("speaker", "")))
        if label:
            proper_nouns.update(part for part in label.split() if part)
    return {normalize_ws(token) for token in proper_nouns if normalize_ws(token)}


def paginate_overlay_text(
    text: str,
    *,
    mode: str,
    delivery: str = "normal",
    proper_nouns: set[str] | None = None,
    overlay_bbox: list[float] | None = None,
    frame_w: int = 1080,
    frame_h: int = 1920,
    decorate_display: bool = True,
) -> list[str]:
    clean = normalize_spanish_text(text)
    if not clean:
        return []
    config = overlay_text_config(
        mode,
        delivery=delivery,
        overlay_bbox=overlay_bbox,
        frame_w=frame_w,
        frame_h=frame_h,
    )
    if overlay_page_is_readable(clean, config, enforce_preferred=False):
        return [clean]
    packed_pages = split_text_sequentially_to_fit(clean, config=config)

    output = [normalize_fragment_text(page) for page in packed_pages if normalize_fragment_text(page)]
    if len(output) > 1 and (mode in {"dialogue", "narration"} or delivery == "shout"):
        output = ensure_decorated_pages_fit(output, config=config)
    if decorate_display and len(output) > 1 and (mode in {"dialogue", "narration"} or delivery == "shout"):
        return decorate_continuation_pages(output)
    return output


def split_caption_blocks(narration: str, max_blocks: int = 3, proper_nouns: set[str] | None = None) -> list[dict]:
    raw = normalize_spanish_text(narration)
    if not raw:
        return []
    raw_pages = paginate_overlay_text(raw, mode="narration", proper_nouns=proper_nouns, decorate_display=False)[:max_blocks]
    display_pages = decorate_continuation_pages(raw_pages) if len(raw_pages) > 1 else list(raw_pages)
    total = len(raw_pages)
    return [
        {
            "text": display_page,
            "raw_text": raw_page,
            "paragraph_index": 0,
            "paragraph_block_index": idx,
            "paragraph_blocks_total": total,
        }
        for idx, (raw_page, display_page) in enumerate(zip(raw_pages, display_pages))
    ]


def split_duration_slots(total_seconds: int, parts: int) -> list[int]:
    total = max(1, int(total_seconds))
    size = max(1, int(parts))
    if size == 1:
        return [total]
    base = total // size
    slots = [base for _ in range(size)]
    remainder = total - (base * size)
    for idx in range(remainder):
        slots[idx] += 1
    for idx, value in enumerate(slots):
        if value <= 0:
            slots[idx] = 1
    adjust = total - sum(slots)
    if adjust != 0:
        slots[-1] += adjust
    return slots


def is_action_shout(text: str) -> bool:
    low = normalize_ws(text).lower()
    if "!" in text:
        return True
    markers = [
        "grita",
        "gritad",
        "fuego",
        "cargad",
        "carga",
        "ahora",
        "atacad",
        "ataque",
        "resistid",
        "corred",
    ]
    return any(token in low for token in markers)


def normalize_dialogue_delivery(delivery: str, line: str) -> str:
    clean = normalize_ws(delivery).lower()
    if clean in {"normal", "shout"}:
        return clean
    return "shout" if is_action_shout(line) else "normal"


def normalize_scene_dialogue(dialogue_payload) -> list[dict]:
    if not isinstance(dialogue_payload, list):
        return []
    out: list[dict] = []
    for item in dialogue_payload:
        if not isinstance(item, dict):
            continue
        speaker = normalize_ws(str(item.get("speaker", "")))
        line = normalize_spanish_text(str(item.get("line", "")))
        if not speaker or not line:
            continue
        out.append(
            {
                "speaker": speaker,
                "line": line,
                "delivery": normalize_dialogue_delivery(str(item.get("delivery", "")), line),
            }
        )
    return out


def load_character_profiles(characters_dir: Path, character_ids: list[str]) -> dict[str, dict]:
    profiles: dict[str, dict] = {}
    for character_id in character_ids:
        path = characters_dir / f"{character_id}.json"
        if not path.exists():
            continue
        try:
            payload = load_json(path)
        except RuntimeError:
            continue
        display_name = normalize_ws(str(payload.get("display_name", ""))).lower()
        voice_profile = normalized_voice_profile(payload.get("voice_profile"))
        if not display_name or not isinstance(voice_profile, dict):
            continue
        profiles[display_name] = voice_profile
        profiles[normalize_ws(character_id).lower()] = voice_profile
    return profiles


def fallback_voice_profile(speaker: str) -> dict:
    low = normalize_ws(speaker).lower()
    narrator_profile = default_narrator_profile()
    profile = normalized_voice_profile(narrator_profile)
    profile["delivery_modifiers"] = dict(narrator_profile["delivery_modifiers"])
    if "guerrero" in low or "capitan" in low:
        profile["tone"] = "marcial_contenido"
        profile["words_per_second"] = 2.7
    elif "obispo" in low:
        profile["tone"] = "liturgico_controlado"
        profile["words_per_second"] = 2.2
    return profile


def resolve_voice_profile(speaker: str, character_profiles: dict[str, dict]) -> dict:
    key = normalize_ws(speaker).lower()
    existing = character_profiles.get(key)
    if isinstance(existing, dict):
        return normalized_voice_profile(existing)
    return fallback_voice_profile(speaker)


def words_in_text(text: str) -> list[str]:
    return re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", normalize_ws(text))


def estimate_block_seconds(text: str, voice_profile: dict, delivery: str) -> int:
    words = len(words_in_text(text))
    narrator_profile = default_narrator_profile()
    modifiers = voice_profile.get("delivery_modifiers") if isinstance(voice_profile, dict) else {}
    try:
        base_wps = float(voice_profile.get("words_per_second", narrator_profile["words_per_second"]))
    except (AttributeError, TypeError, ValueError):
        base_wps = float(narrator_profile["words_per_second"])
    try:
        delivery_factor = float(modifiers.get(delivery, modifiers.get("normal", 1.0))) if isinstance(modifiers, dict) else 1.0
    except (TypeError, ValueError):
        delivery_factor = 1.0
    effective_wps = max(1.2, base_wps * delivery_factor)
    commas = len(re.findall(r"[,;:]", text))
    sentence_ends = len(re.findall(r"[.!?]", text))
    duration = (words / effective_wps) + (0.18 * commas) + (0.28 * sentence_ends) + 0.35
    if words <= 3:
        duration = max(duration, 1.25)
    elif words <= 8:
        duration = max(duration, 2.0)
    return max(1, round(duration))


def build_scene_block_plan(
    scene: dict,
    narration: str,
    scene_dialogue: list[dict],
    character_profiles: dict[str, dict],
) -> list[dict]:
    narration = normalize_spanish_text(narration)
    proper_nouns = collect_scene_proper_nouns(scene, scene_dialogue)
    if scene_dialogue:
        narrator_profile = default_narrator_profile()
        narration_raw_pages = paginate_overlay_text(
            narration,
            mode="narration",
            proper_nouns=proper_nouns,
            decorate_display=False,
        ) or [narration]
        narration_display_pages = (
            decorate_continuation_pages(narration_raw_pages)
            if len(narration_raw_pages) > 1
            else list(narration_raw_pages)
        )
        blocks: list[dict] = []
        for page_idx, (raw_page_text, display_page_text) in enumerate(zip(narration_raw_pages, narration_display_pages)):
            block_index = len(blocks) + 1
            focus_anchor = infer_focus_anchor(scene, block_index)
            primary_actor = infer_primary_actor(scene, "")
            blocks.append(
                {
                    "text": display_page_text,
                    "source_text": narration,
                    "paragraph_index": 0,
                    "paragraph_block_index": page_idx,
                    "paragraph_blocks_total": len(narration_raw_pages),
                    "dialogue_speaker": "",
                    "dialogue_line": "",
                    "dialogue_delivery": "normal",
                    "audio_text": raw_page_text,
                    "voice_profile": dict(narrator_profile),
                    "duration_seconds": estimate_block_seconds(raw_page_text, narrator_profile, "normal"),
                    "primary_actor": primary_actor,
                    "focus_anchor": focus_anchor,
                    "bubble_anchor": "",
                    "proper_nouns": sorted(proper_nouns),
                }
            )
        for idx, row in enumerate(scene_dialogue, start=1):
            speaker = str(row.get("speaker", "")).strip()
            line = normalize_spanish_text(str(row.get("line", "")).strip())
            delivery = str(row.get("delivery", "normal")).strip() or "normal"
            voice_profile = resolve_voice_profile(speaker, character_profiles)
            dialogue_raw_pages = paginate_overlay_text(
                line,
                mode="dialogue",
                delivery=delivery,
                proper_nouns=proper_nouns,
                decorate_display=False,
            ) or [line]
            dialogue_display_pages = (
                decorate_continuation_pages(dialogue_raw_pages)
                if len(dialogue_raw_pages) > 1
                else list(dialogue_raw_pages)
            )
            for page_idx, (raw_page_text, display_page_text) in enumerate(zip(dialogue_raw_pages, dialogue_display_pages)):
                block_index = len(blocks) + 1
                focus_anchor = infer_focus_anchor(scene, block_index, dialogue_speaker=speaker)
                blocks.append(
                    {
                        "text": display_page_text or narration,
                        "source_text": line,
                        "paragraph_index": idx,
                        "paragraph_block_index": page_idx,
                        "paragraph_blocks_total": len(dialogue_raw_pages),
                        "dialogue_speaker": speaker,
                        "dialogue_line": display_page_text,
                        "dialogue_delivery": delivery,
                        "audio_text": raw_page_text,
                        "voice_profile": voice_profile,
                        "duration_seconds": estimate_block_seconds(raw_page_text, voice_profile, delivery),
                        "primary_actor": infer_primary_actor(scene, speaker),
                        "focus_anchor": focus_anchor,
                        "bubble_anchor": bubble_anchor_for_focus(focus_anchor, block_index),
                        "proper_nouns": sorted(proper_nouns),
                    }
                )
        return blocks

    caption_blocks = split_caption_blocks(narration, max_blocks=999, proper_nouns=proper_nouns)
    if not caption_blocks:
        caption_blocks = [
            {
                "text": normalize_spanish_text(narration),
                "paragraph_index": 0,
                "paragraph_block_index": 0,
                "paragraph_blocks_total": 1,
            }
        ]
    blocks = []
    narrator_profile = default_narrator_profile()
    for item in caption_blocks:
        voice_profile = dict(narrator_profile)
        voice_profile["delivery_modifiers"] = dict(narrator_profile["delivery_modifiers"])
        text = str(item.get("text", "")).strip()
        raw_text = str(item.get("raw_text", text)).strip()
        block_index = len(blocks) + 1
        focus_anchor = infer_focus_anchor(scene, block_index)
        blocks.append(
            {
                "text": text,
                "source_text": narration,
                "paragraph_index": int(item.get("paragraph_index", 0)),
                "paragraph_block_index": int(item.get("paragraph_block_index", 0)),
                "paragraph_blocks_total": int(item.get("paragraph_blocks_total", len(caption_blocks))),
                "dialogue_speaker": "",
                "dialogue_line": "",
                "dialogue_delivery": "normal",
                "audio_text": raw_text,
                "voice_profile": voice_profile,
                "duration_seconds": estimate_block_seconds(raw_text, voice_profile, "normal"),
                "primary_actor": infer_primary_actor(scene, ""),
                "focus_anchor": focus_anchor,
                "bubble_anchor": "",
                "proper_nouns": sorted(proper_nouns),
            }
        )
    return blocks


def repaginate_phase_group_for_layout(
    group: list[dict],
    *,
    image_size: dict,
) -> list[dict]:
    if not group:
        return []
    first = group[0]
    kind = "dialogue" if normalize_ws(str(first.get("dialogue_line", ""))) else "narration"
    source_text = normalize_spanish_text(str(first.get("source_text") or first.get("dialogue_line") or first.get("text") or ""))
    if not source_text:
        return [dict(item) for item in group]

    proper_nouns = set(first.get("proper_nouns", [])) if isinstance(first.get("proper_nouns"), list) else set()
    pages = paginate_overlay_text(
        source_text,
        mode=kind,
        delivery=str(first.get("dialogue_delivery", "normal")),
        proper_nouns=proper_nouns,
        overlay_bbox=list(first.get("overlay_bbox", [])) if isinstance(first.get("overlay_bbox"), list) else None,
        frame_w=max(1, int(image_size.get("width", 1080))),
        frame_h=max(1, int(image_size.get("height", 1920))),
        decorate_display=False,
    ) or [source_text]
    display_pages = decorate_continuation_pages(pages) if len(pages) > 1 else list(pages)

    output: list[dict] = []
    for page_idx, (raw_page_text, display_page_text) in enumerate(zip(pages, display_pages)):
        item = dict(first)
        item["text"] = display_page_text
        item["source_text"] = source_text
        item["paragraph_block_index"] = page_idx
        item["paragraph_blocks_total"] = len(pages)
        if kind == "dialogue":
            item["dialogue_line"] = raw_page_text
            item["audio_text"] = raw_page_text
        else:
            item["dialogue_line"] = ""
            item["audio_text"] = raw_page_text
        voice_profile = item.get("voice_profile") if isinstance(item.get("voice_profile"), dict) else default_narrator_profile()
        delivery = str(item.get("dialogue_delivery", "normal"))
        item["duration_seconds"] = estimate_block_seconds(raw_page_text, voice_profile, delivery)
        output.append(item)
    return output


def repaginate_scene_blocks_to_layout(
    scene: dict,
    phases: list[dict],
    layout_analysis: dict,
) -> tuple[dict, list[dict], bool]:
    image_size = dict(layout_analysis.get("image_size") or {"width": 1080, "height": 1920})
    repaginated: list[dict] = []
    changed = False
    idx = 0
    while idx < len(phases):
        phase = phases[idx]
        kind = "dialogue" if normalize_ws(str(phase.get("dialogue_line", ""))) else "narration"
        source_text = normalize_spanish_text(str(phase.get("source_text") or phase.get("dialogue_line") or phase.get("text") or ""))
        speaker = normalize_ws(str(phase.get("dialogue_speaker", "")))
        paragraph_index = int(phase.get("paragraph_index", -1))
        group = [phase]
        idx += 1
        while idx < len(phases):
            candidate = phases[idx]
            candidate_kind = "dialogue" if normalize_ws(str(candidate.get("dialogue_line", ""))) else "narration"
            candidate_source = normalize_spanish_text(
                str(candidate.get("source_text") or candidate.get("dialogue_line") or candidate.get("text") or "")
            )
            candidate_speaker = normalize_ws(str(candidate.get("dialogue_speaker", "")))
            candidate_paragraph = int(candidate.get("paragraph_index", -1))
            if (
                candidate_kind != kind
                or candidate_source != source_text
                or candidate_speaker != speaker
                or candidate_paragraph != paragraph_index
            ):
                break
            group.append(candidate)
            idx += 1

        expanded_group = repaginate_phase_group_for_layout(group, image_size=image_size)
        if len(expanded_group) != len(group):
            changed = True
        else:
            for original, updated in zip(group, expanded_group):
                original_text = normalize_spanish_text(str(original.get("dialogue_line") or original.get("text") or ""))
                updated_text = normalize_spanish_text(str(updated.get("dialogue_line") or updated.get("text") or ""))
                if original_text != updated_text:
                    changed = True
                    break
        repaginated.extend(expanded_group)

    for phase_index, item in enumerate(repaginated, start=1):
        item["phase_index"] = phase_index
    assigned_layout, assigned_phases = assign_scene_layout(scene, repaginated, layout_analysis)
    return assigned_layout, assigned_phases, changed


def build_image_prompt(episode: dict, scene: dict) -> str:
    episode_title = str(episode.get("title", "")).strip()
    scene_prompt = str(scene.get("visual_prompt", "")).strip()
    scene_narration = str(scene.get("narration", "")).strip()
    characters = episode.get("characters") or []
    cast = ", ".join(str(x) for x in characters[:5]) if characters else "sin personajes nombrados"
    dialogue = normalize_scene_dialogue(scene.get("dialogue"))
    dialogue_hint = "; ".join(f"{row['speaker']}: {row['line']}" for row in dialogue[:2]) or "sin dialogo directo"

    style_guide = (
        "Ilustracion historica cinematografica con lenguaje de comic, alta fidelidad, tono documental, "
        "sin texto incrustado, sin logos, sin watermark, sin UI. "
        "Vestuario altomedieval iberico coherente. "
        "Composicion vertical para video 9:16, profundidad dramatica, encuadre de vineta. "
        "Mantener el dibujo historico, pero con paleta mas viva, contrastes limpios, azules, rojos y dorados intensos. "
        "Evitar sepia, apagado, envejecido excesivo o aspecto deslavado."
    )

    return (
        f"TITULO EPISODIO: {episode_title}\n"
        f"ESCENA {scene.get('scene_index')}: {scene_prompt}\n"
        f"CONTEXTO NARRATIVO: {scene_narration}\n"
        f"DIALOGO REFERENCIA: {dialogue_hint}\n"
        f"ELENCO REFERENCIA: {cast}\n"
        f"ESTILO OBLIGATORIO: {style_guide}"
    )


def build_block_image_prompt(
    base_prompt: str,
    block_text: str,
    dialogue_speaker: str,
    dialogue_line: str,
    block_index: int,
    total_blocks: int,
    prev_scene_narration: str,
    next_scene_narration: str,
    primary_actor: str,
    focus_anchor: str,
    bubble_anchor: str,
) -> str:
    dialogue_ref = f"{dialogue_speaker}: {dialogue_line}" if dialogue_speaker and dialogue_line else "sin dialogo adicional"
    speaker_visual = (
        f"Mostrar claramente a {dialogue_speaker} pronunciando su frase, con expresion facial y gestual coherente."
        if dialogue_speaker and dialogue_line
        else "No hay parlamento directo en este bloque."
    )
    focus_instruction_map = {
        "upper_left": "Situa el foco principal de la accion y el rostro del actor principal en el tercio superior izquierdo.",
        "upper_center": "Situa el foco principal de la accion y el rostro del actor principal en la franja superior central.",
        "upper_right": "Situa el foco principal de la accion y el rostro del actor principal en el tercio superior derecho.",
    }
    bubble_instruction_map = {
        "upper_left": "Reserva espacio limpio en el tercio superior izquierdo para el bocadillo, sin tapar caras ni manos clave.",
        "upper_right": "Reserva espacio limpio en el tercio superior derecho para el bocadillo, sin tapar caras ni manos clave.",
    }
    primary_actor_note = (
        f"El actor principal visual de este bloque es {primary_actor}."
        if primary_actor
        else "No hay un actor unico; prioriza el foco de la accion colectiva."
    )
    return (
        f"{base_prompt}\n"
        f"MOMENTO {block_index}/{total_blocks}: {block_text}\n"
        f"REACCION PERSONAJE: {dialogue_ref}\n"
        f"FOCO VISUAL DIALOGO: {speaker_visual}\n"
        f"ACTOR PRINCIPAL: {primary_actor_note}\n"
        f"ANCLA DE FOCO: {focus_instruction_map.get(focus_anchor, focus_instruction_map['upper_center'])}\n"
        f"ESPACIO DE BOCADILLO: {bubble_instruction_map.get(bubble_anchor, 'No anadir bocadillo; mantener limpia la parte superior de la composicion.')}\n"
        "INDICACIONES EXTRA: accion intensa, composicion compacta, expresiones faciales fuertes, "
        "color vivo y luminoso, energia de comic historico, sin aspecto de postal, sin tono envejecido, sin filtros sepia.\n"
        f"CONTINUIDAD ESCENA ANTERIOR: {prev_scene_narration or 'inicio de secuencia'}\n"
        f"CONTINUIDAD ESCENA SIGUIENTE: {next_scene_narration or 'cierre de secuencia'}"
    )


def build_scene_image_prompt(
    base_prompt: str,
    scene_blocks: list[dict],
    prev_scene_narration: str,
    next_scene_narration: str,
) -> str:
    actor_labels: list[str] = []
    phase_hints: list[str] = []
    for block in scene_blocks[:6]:
        actor = normalize_ws(str(block.get("primary_actor", "")))
        if actor and actor not in actor_labels:
            actor_labels.append(actor)
        text = normalize_ws(str(block.get("dialogue_line") or block.get("text") or ""))
        if text:
            phase_hints.append(text)
    actor_note = ", ".join(actor_labels[:3]) or "sin actor unico"
    phase_summary = " | ".join(phase_hints[:4]) or "sin fase destacada"
    return (
        f"{base_prompt}\n"
        "IMAGEN UNICA DE ESCENA: esta ilustracion debe sostener toda la escena mientras cambian bocadillos y narracion.\n"
        f"ACTORES CLAVE: {actor_note}\n"
        f"FRASES Y MOMENTOS A SOSTENER: {phase_summary}\n"
        "COMPOSICION: una sola ilustracion fuerte, legible y estable, con capas de accion claras. "
        "No fragmentar la escena en momentos incompatibles entre si.\n"
        f"CONTINUIDAD ESCENA ANTERIOR: {prev_scene_narration or 'inicio de secuencia'}\n"
        f"CONTINUIDAD ESCENA SIGUIENTE: {next_scene_narration or 'cierre de secuencia'}"
    )


def get_field(obj, name: str):
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def first_image_bytes(response) -> bytes:
    data = get_field(response, "data")
    if not data:
        raise RuntimeError("Image API response has no data entries.")
    first = data[0]
    b64 = get_field(first, "b64_json")
    if b64:
        return base64.b64decode(b64)
    url = get_field(first, "url")
    if url:
        with urllib.request.urlopen(url) as req:
            return req.read()
    raise RuntimeError("Image API response has neither b64_json nor url.")


def save_tts_audio(
    client,
    model: str,
    voice: str,
    narration: str,
    output_audio: Path,
    *,
    instructions: str | None = None,
    speed: float | None = None,
) -> None:
    speech = client.audio.speech
    request = {
        "model": model,
        "voice": voice,
        "input": narration,
        "response_format": "mp3",
    }
    if instructions:
        request["instructions"] = instructions
    if speed is not None:
        request["speed"] = speed

    fallback_request = {
        "model": model,
        "voice": voice,
        "input": narration,
        "response_format": "mp3",
    }

    # Preferred SDK path: avoids deprecation warning from non-streaming stream_to_file.
    with_streaming = getattr(speech, "with_streaming_response", None)
    if with_streaming is not None and hasattr(with_streaming, "create"):
        try:
            with with_streaming.create(**request) as tts_stream:
                if hasattr(tts_stream, "stream_to_file"):
                    tts_stream.stream_to_file(output_audio.as_posix())
                    return
                if hasattr(tts_stream, "read"):
                    content = tts_stream.read()
                    if isinstance(content, (bytes, bytearray)):
                        output_audio.write_bytes(bytes(content))
                        return
        except Exception as exc:
            msg = str(exc).lower()
            unsupported = "unexpected keyword" in msg or "unknown parameter" in msg or "instructions" in msg or "speed" in msg
            if not unsupported:
                raise
            with with_streaming.create(**fallback_request) as tts_stream:
                if hasattr(tts_stream, "stream_to_file"):
                    tts_stream.stream_to_file(output_audio.as_posix())
                    return
                if hasattr(tts_stream, "read"):
                    content = tts_stream.read()
                    if isinstance(content, (bytes, bytearray)):
                        output_audio.write_bytes(bytes(content))
                        return
        raise RuntimeError("Unsupported streaming TTS response format from SDK.")

    # Backward compatibility for older SDK shapes.
    try:
        tts_response = speech.create(**request)
    except Exception as exc:
        msg = str(exc).lower()
        unsupported = "unexpected keyword" in msg or "unknown parameter" in msg or "instructions" in msg or "speed" in msg
        if not unsupported:
            raise
        tts_response = speech.create(**fallback_request)
    if hasattr(tts_response, "write_to_file"):
        tts_response.write_to_file(output_audio.as_posix())
        return
    if hasattr(tts_response, "stream_to_file"):
        tts_response.stream_to_file(output_audio.as_posix())
        return
    content = get_field(tts_response, "content")
    if isinstance(content, (bytes, bytearray)):
        output_audio.write_bytes(bytes(content))
        return
    raise RuntimeError("Unsupported TTS response format from SDK.")


def probe_audio_duration(ffmpeg_bin: str, audio_path: Path) -> float:
    ffprobe = Path(ffmpeg_bin).with_name("ffprobe")
    cmd = [
        ffprobe.as_posix(),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        audio_path.as_posix(),
    ]
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return max(0.01, float((proc.stdout or "0").strip() or "0"))


def atempo_chain(speed_factor: float) -> str:
    factor = max(0.5, min(100.0, speed_factor))
    parts: list[float] = []
    while factor > 2.0:
        parts.append(2.0)
        factor /= 2.0
    while factor < 0.5:
        parts.append(0.5)
        factor /= 0.5
    parts.append(factor)
    return ",".join(f"atempo={part:.5f}" for part in parts)


def fit_audio_to_duration(ffmpeg: str, input_audio: Path, output_audio: Path, target_seconds: int) -> None:
    target = max(1, int(target_seconds))
    current = probe_audio_duration(ffmpeg, input_audio)
    ratio = max(0.5, min(8.0, current / target))
    chain = atempo_chain(ratio)
    af = f"{chain},apad=pad_dur={target},atrim=0:{target}"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        input_audio.as_posix(),
        "-filter:a",
        af,
        "-q:a",
        "2",
        output_audio.as_posix(),
    ]
    subprocess.run(cmd, check=True)


def concat_audio_segments(ffmpeg: str, concat_paths: list[Path], output_audio: Path, work_dir: Path) -> None:
    concat_list = work_dir / f"{output_audio.stem}.concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{path.resolve().as_posix()}'" for path in concat_paths) + "\n",
        encoding="utf-8",
    )
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_list.as_posix(),
        "-c:a",
        "libmp3lame",
        "-q:a",
        "2",
        output_audio.as_posix(),
    ]
    subprocess.run(cmd, check=True)


def ensure_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    raise RuntimeError("ffmpeg not found. Install ffmpeg first.")


def normalize_image_quality(value: str | None) -> str | None:
    if value is None:
        return None
    clean = normalize_ws(str(value)).lower()
    if not clean:
        return None
    if clean not in {"low", "medium", "high", "auto"}:
        raise RuntimeError("Invalid image quality. Use one of: low, medium, high, auto.")
    return clean


def is_billing_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "billing_hard_limit_reached" in msg
        or "billing hard limit has been reached" in msg
        or "billing_limit_user_error" in msg
    )


def openai_error_message(exc: Exception) -> str:
    if is_billing_limit_error(exc):
        return (
            "OpenAI API rejected the request: billing hard limit reached.\n"
            "Action required:\n"
            "1) Open https://platform.openai.com/settings/organization/billing/overview\n"
            "2) Verify payment method / add credits\n"
            "3) Raise hard usage limit in billing limits\n"
            "4) Retry this command\n"
            "Temporary workaround: run with --mock or --fallback-mock-on-billing-error."
        )
    return f"OpenAI API error: {exc}"


def create_mock_image(
    ffmpeg: str,
    output_png: Path,
    width: int = 1080,
    height: int = 1920,
    color: str = "#1f2937",
) -> None:
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={width}x{height}",
        "-update",
        "1",
        "-frames:v",
        "1",
        output_png.as_posix(),
    ]
    subprocess.run(cmd, check=True)


def create_mock_audio(ffmpeg: str, output_mp3: Path, duration_s: int) -> None:
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t",
        str(max(1, int(duration_s))),
        "-q:a",
        "9",
        "-acodec",
        "libmp3lame",
        output_mp3.as_posix(),
    ]
    subprocess.run(cmd, check=True)


def print_mode_warning(mock_mode: bool, fallback_mock_on_billing_error: bool) -> None:
    if mock_mode:
        print(
            "WARNING: generate_scene_assets.py is running with --mock. No OpenAI API calls will be made for image, TTS, or layout analysis.",
            file=sys.stderr,
        )
        return
    if fallback_mock_on_billing_error:
        print(
            "WARNING: generate_scene_assets.py uses OpenAI APIs for image, TTS, and optional layout analysis.\n"
            "This run will call the API unless a billing hard limit error triggers fallback mock assets.\n"
            "To disable OpenAI calls entirely, rerun with --mock.",
            file=sys.stderr,
        )
        return
    print(
        "WARNING: generate_scene_assets.py uses OpenAI APIs for image, TTS, and optional layout analysis.\n"
        "To disable OpenAI calls entirely, rerun with --mock.",
        file=sys.stderr,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", required=True, help="Path to episode JSON")
    parser.add_argument("--assets-dir", default=str(DEFAULT_ASSETS_DIR), help="Root output directory for generated assets")
    parser.add_argument("--dotenv", default=str(ROOT / ".env"), help="Path to .env file for local execution")
    parser.add_argument("--characters-dir", default=str(DEFAULT_CHAR_DIR), help="Directory of character JSON files")
    parser.add_argument("--image-model", default=None, help="OpenAI image model")
    parser.add_argument("--image-size", default=None, help="Image size, e.g. 1024x1024")
    parser.add_argument("--image-quality", default=None, help="OpenAI image quality: low, medium, high or auto")
    parser.add_argument("--tts-model", default=None, help="OpenAI TTS model")
    parser.add_argument("--tts-voice", default=None, help="OpenAI TTS voice id")
    parser.add_argument("--mock", action="store_true", help="Generate placeholder assets with ffmpeg instead of OpenAI APIs")
    parser.add_argument(
        "--fallback-mock-on-billing-error",
        action="store_true",
        help="If OpenAI billing limit is hit, continue by generating placeholder assets for affected scenes.",
    )
    args = parser.parse_args()

    try:
        episode_path = Path(args.episode)
        episode = load_json(episode_path)
        scenes = episode.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise RuntimeError("Episode has no scenes.")
        character_profiles = load_character_profiles(Path(args.characters_dir), [str(x) for x in episode.get("characters", [])])

        assets_root = Path(args.assets_dir) / str(episode["episode_id"])
        scenes_dir = assets_root / "scenes"
        scenes_dir.mkdir(parents=True, exist_ok=True)

        load_dotenv_if_present(Path(args.dotenv))
        ffmpeg = ensure_ffmpeg()
        print_mode_warning(args.mock, args.fallback_mock_on_billing_error)
        image_model = args.image_model or os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
        image_size = args.image_size or os.getenv("OPENAI_IMAGE_SIZE", "1024x1024")
        image_quality = normalize_image_quality(args.image_quality or os.getenv("OPENAI_IMAGE_QUALITY"))
        tts_model = args.tts_model or os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        tts_voice = normalize_tts_voice_id(args.tts_voice or os.getenv("OPENAI_TTS_VOICE", "alloy"))
        layout_model = os.getenv("OPENAI_LAYOUT_MODEL", os.getenv("OPENAI_EPISODE_MODEL", "gpt-5.4"))
        layout_reasoning_effort = os.getenv("OPENAI_LAYOUT_REASONING_EFFORT", "low")
        narrator_profile = default_narrator_profile()

        client = None
        if not args.mock:
            api_key = os.getenv("OPENAI_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is missing. Set it in env or use --mock.")
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError("Missing dependency 'openai'. Install with: python3 -m pip install openai") from exc
            client = OpenAI(
                api_key=api_key,
                organization=os.getenv("OPENAI_ORGANIZATION") or None,
                project=os.getenv("OPENAI_PROJECT") or None,
            )

        manifest_scenes: list[dict] = []
        used_billing_fallback = False
        for scene_pos, scene in enumerate(scenes):
            idx = int(scene["scene_index"])
            narration = str(scene["narration"]).strip()
            visual_prompt = str(scene["visual_prompt"]).strip()
            scene_dialogue = normalize_scene_dialogue(scene.get("dialogue"))
            prev_narration = ""
            next_narration = ""
            if scene_pos > 0:
                prev_narration = str(scenes[scene_pos - 1].get("narration", "")).strip()
            if scene_pos + 1 < len(scenes):
                next_narration = str(scenes[scene_pos + 1].get("narration", "")).strip()

            audio_path = scenes_dir / f"scene_{idx:02d}.mp3"
            scene_image_path = scenes_dir / f"scene_{idx:02d}.png"
            base_prompt_path = scenes_dir / f"scene_{idx:02d}.prompt.txt"
            base_prompt = build_image_prompt(episode, scene)
            base_prompt_path.write_text(base_prompt + "\n", encoding="utf-8")

            scene_blocks = build_scene_block_plan(scene, narration, scene_dialogue, character_profiles)
            scene_prompt = build_scene_image_prompt(
                base_prompt=base_prompt,
                scene_blocks=scene_blocks,
                prev_scene_narration=prev_narration,
                next_scene_narration=next_narration,
            )
            scene_prompt_path = scenes_dir / f"scene_{idx:02d}.image.prompt.txt"
            scene_prompt_path.write_text(scene_prompt + "\n", encoding="utf-8")

            text_phases: list[dict] = []
            for phase_idx, block_payload in enumerate(scene_blocks, start=1):
                phase_text = str(block_payload.get("text", "")).strip()
                phase_line = str(block_payload.get("dialogue_line", "")).strip()
                text_phases.append(
                    {
                        "phase_index": phase_idx,
                        "text": phase_text,
                        "source_text": str(block_payload.get("source_text", phase_line or phase_text)).strip(),
                        "paragraph_index": int(block_payload.get("paragraph_index", 0)),
                        "paragraph_block_index": int(block_payload.get("paragraph_block_index", 0)),
                        "paragraph_blocks_total": int(block_payload.get("paragraph_blocks_total", 1)),
                        "duration_seconds": int(block_payload.get("duration_seconds", 1)),
                        "audio_path": "",
                        "audio_text": str(block_payload.get("audio_text", phase_line or phase_text)),
                        "dialogue_speaker": str(block_payload.get("dialogue_speaker", "")).strip(),
                        "dialogue_line": phase_line,
                        "dialogue_delivery": str(block_payload.get("dialogue_delivery", "normal")).strip() or "normal",
                        "voice_profile": dict(block_payload.get("voice_profile", narrator_profile)),
                        "primary_actor": str(block_payload.get("primary_actor", "")).strip(),
                        "focus_anchor": str(block_payload.get("focus_anchor", "upper_center")).strip() or "upper_center",
                        "bubble_anchor": str(block_payload.get("bubble_anchor", "")).strip(),
                        "proper_nouns": list(block_payload.get("proper_nouns", [])) if isinstance(block_payload.get("proper_nouns"), list) else [],
                    }
                )
            scene_uses_mock_layout = bool(args.mock)
            scene_force_mock_assets = bool(args.mock)
            if args.mock:
                color = MOCK_SCENE_COLORS[idx % len(MOCK_SCENE_COLORS)]
                create_mock_image(ffmpeg, scene_image_path, color=color)
            else:
                try:
                    image_resp = client.images.generate(
                        model=image_model,
                        prompt=scene_prompt,
                        size=image_size,
                        quality=image_quality,
                    )
                    scene_image_path.write_bytes(first_image_bytes(image_resp))
                    concat_audio_segments(
                        ffmpeg,
                        [Path(str(phase["audio_path"])) for phase in text_phases],
                        audio_path,
                        scenes_dir,
                    )
                except Exception as exc:  # SDK raises typed exceptions; keep generic for compatibility.
                    if args.fallback_mock_on_billing_error and is_billing_limit_error(exc):
                        print(
                            "WARNING: OpenAI billing limit reached. "
                            f"Using mock assets for scene {idx:02d} of {episode['episode_id']}."
                        )
                        color = MOCK_SCENE_COLORS[idx % len(MOCK_SCENE_COLORS)]
                        create_mock_image(ffmpeg, scene_image_path, color=color)
                        scene_uses_mock_layout = True
                        scene_force_mock_assets = True
                        used_billing_fallback = True
                    else:
                        raise RuntimeError(openai_error_message(exc)) from exc

            layout_analysis, text_phases = analyze_and_assign_layout(
                image_path=scene_image_path,
                scene=scene,
                phases=text_phases,
                client=client,
                model=layout_model,
                reasoning_effort=layout_reasoning_effort,
                use_model=bool(client and not scene_uses_mock_layout),
            )
            for _ in range(3):
                layout_analysis, text_phases, changed = repaginate_scene_blocks_to_layout(
                    scene,
                    text_phases,
                    layout_analysis,
                )
                if not changed:
                    break

            for phase_idx, phase in enumerate(text_phases, start=1):
                phase_audio = scenes_dir / f"scene_{idx:02d}_phase_{phase_idx:02d}.mp3"
                phase["phase_index"] = phase_idx
                phase["audio_path"] = str(phase_audio)
                phase["audio_text"] = str(phase.get("audio_text") or phase.get("dialogue_line") or phase.get("text") or "")

            duration = sum(int(phase["duration_seconds"]) for phase in text_phases)
            if duration <= 0:
                duration = max(1, int(scene.get("estimated_seconds", 1)))

            if scene_force_mock_assets:
                for phase in text_phases:
                    create_mock_audio(ffmpeg, Path(str(phase["audio_path"])), int(phase["duration_seconds"]))
            else:
                try:
                    for phase in text_phases:
                        profile = phase.get("voice_profile") if isinstance(phase.get("voice_profile"), dict) else narrator_profile
                        target_seconds = int(phase["duration_seconds"])
                        raw_audio = Path(str(phase["audio_path"])).with_suffix(".raw.mp3")
                        profile_tone = str(profile.get("tone", narrator_profile["tone"])).replace("_", " ")
                        delivery = str(phase.get("dialogue_delivery", "normal"))
                        instructions = f"Habla con tono {profile_tone}. Ritmo controlado y coherente con una narracion historica."
                        if delivery == "shout":
                            instructions += " Entrega la frase con intensidad y urgencia, sin perder claridad."
                        try:
                            profile_wps = float(profile.get("words_per_second", narrator_profile["words_per_second"]))
                        except (TypeError, ValueError):
                            profile_wps = float(narrator_profile["words_per_second"])
                        speed_hint = max(0.8, min(1.35, profile_wps / float(narrator_profile["words_per_second"])))
                        save_tts_audio(
                            client=client,
                            model=tts_model,
                            voice=normalize_tts_voice_id(
                                str(profile.get("tts_voice", tts_voice or narrator_profile["tts_voice"])),
                                tts_voice or narrator_profile["tts_voice"],
                            ),
                            narration=str(phase["audio_text"]),
                            output_audio=raw_audio,
                            instructions=instructions,
                            speed=speed_hint,
                        )
                        fit_audio_to_duration(ffmpeg, raw_audio, Path(str(phase["audio_path"])), target_seconds)
                except Exception as exc:  # SDK raises typed exceptions; keep generic for compatibility.
                    if args.fallback_mock_on_billing_error and is_billing_limit_error(exc):
                        print(
                            "WARNING: OpenAI billing limit reached while generating audio. "
                            f"Using mock audio for scene {idx:02d} of {episode['episode_id']}."
                        )
                        for phase in text_phases:
                            create_mock_audio(ffmpeg, Path(str(phase["audio_path"])), int(phase["duration_seconds"]))
                        used_billing_fallback = True
                    else:
                        raise RuntimeError(openai_error_message(exc)) from exc

            concat_audio_segments(
                ffmpeg,
                [Path(str(phase["audio_path"])) for phase in text_phases],
                audio_path,
                scenes_dir,
            )

            prompt_paths = [str(scene_prompt_path)]
            image_paths = [str(scene_image_path)]
            for phase in text_phases:
                phase["image_path"] = str(scene_image_path)

            manifest_scenes.append(
                {
                    "scene_index": idx,
                    "estimated_seconds": duration,
                    "narration": narration,
                    "visual_prompt": visual_prompt,
                    "dialogue": scene_dialogue,
                    "scene_image_path": str(scene_image_path),
                    "layout_analysis": layout_analysis,
                    "camera_track": dict(layout_analysis.get("camera_track") or {}),
                    "text_phases": [
                        {
                            "phase_index": int(phase["phase_index"]),
                            "block_index": int(phase["phase_index"]),
                            "phase_kind": str(phase.get("phase_kind", "narration")),
                            "text": str(phase["text"]),
                            "paragraph_index": int(phase.get("paragraph_index", 0)),
                            "paragraph_block_index": int(phase.get("paragraph_block_index", 0)),
                            "paragraph_blocks_total": int(phase.get("paragraph_blocks_total", 1)),
                            "duration_seconds": int(phase["duration_seconds"]),
                            "phase_start_s": float(phase.get("phase_start_s", 0.0)),
                            "phase_end_s": float(phase.get("phase_end_s", 0.0)),
                            "image_path": str(scene_image_path),
                            "audio_path": str(phase["audio_path"]),
                            "audio_text": str(phase.get("audio_text", "")),
                            "dialogue_speaker": str(phase.get("dialogue_speaker", "")),
                            "dialogue_line": str(phase.get("dialogue_line", "")),
                            "dialogue_delivery": str(phase.get("dialogue_delivery", "normal")),
                            "primary_actor": str(phase.get("primary_actor", "")),
                            "proper_nouns": list(phase.get("proper_nouns", [])) if isinstance(phase.get("proper_nouns"), list) else [],
                            "overlay_bbox": list(phase.get("overlay_bbox", [])) if isinstance(phase.get("overlay_bbox"), list) else [],
                            "focus_bbox": list(phase.get("focus_bbox", [])) if isinstance(phase.get("focus_bbox"), list) else [],
                            "prompt_path": str(scene_prompt_path),
                        }
                        for phase in text_phases
                    ],
                    "caption_blocks": [
                        {
                            "block_index": int(phase["phase_index"]),
                            "text": str(phase["text"]),
                            "paragraph_index": int(phase.get("paragraph_index", 0)),
                            "paragraph_block_index": int(phase.get("paragraph_block_index", 0)),
                            "paragraph_blocks_total": int(phase.get("paragraph_blocks_total", 1)),
                            "duration_seconds": int(phase["duration_seconds"]),
                            "phase_start_s": float(phase.get("phase_start_s", 0.0)),
                            "phase_end_s": float(phase.get("phase_end_s", 0.0)),
                            "image_path": str(scene_image_path),
                            "audio_path": str(phase["audio_path"]),
                            "dialogue_speaker": str(phase.get("dialogue_speaker", "")),
                            "dialogue_line": str(phase.get("dialogue_line", "")),
                            "dialogue_delivery": str(phase.get("dialogue_delivery", "normal")),
                            "primary_actor": str(phase.get("primary_actor", "")),
                            "proper_nouns": list(phase.get("proper_nouns", [])) if isinstance(phase.get("proper_nouns"), list) else [],
                            "overlay_bbox": list(phase.get("overlay_bbox", [])) if isinstance(phase.get("overlay_bbox"), list) else [],
                            "focus_bbox": list(phase.get("focus_bbox", [])) if isinstance(phase.get("focus_bbox"), list) else [],
                            "prompt_path": str(scene_prompt_path),
                        }
                        for phase in text_phases
                    ],
                    "image_path": str(scene_image_path),
                    "image_paths": image_paths,
                    "audio_path": str(audio_path),
                    "prompt_path": str(scene_prompt_path),
                    "prompt_paths": prompt_paths,
                }
            )

        manifest = {
            "episode_id": episode["episode_id"],
            "episode_file": str(episode_path),
            "created_at": now_iso(),
            "generator": {
                "mock_mode": bool(args.mock),
                "billing_fallback_used": bool(used_billing_fallback),
                "image_model": image_model if not args.mock else None,
                "image_size": image_size if not args.mock else None,
                "image_quality": image_quality if not args.mock else None,
                "layout_model": layout_model if not args.mock else None,
                "layout_reasoning_effort": layout_reasoning_effort if not args.mock else None,
                "tts_model": tts_model if not args.mock else None,
                "tts_voice": tts_voice if not args.mock else None,
            },
            "scenes": manifest_scenes,
        }
        manifest_path = assets_root / "manifest.json"
        dump_json(manifest_path, manifest)

        print(f"Generated scene assets: {assets_root}")
        print(f"Manifest: {manifest_path}")
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: ffmpeg command failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
