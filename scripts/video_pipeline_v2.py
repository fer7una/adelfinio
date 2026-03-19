#!/usr/bin/env python3
"""Shared implementation for the V2 scene-first video pipeline."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import unicodedata
from difflib import SequenceMatcher
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from pipeline_common import ROOT, build_openai_client, load_dotenv_if_present, read_json, write_json
    from generate_scene_assets import (
        create_mock_audio,
        default_narrator_profile,
        normalize_tts_voice_id,
        normalized_voice_profile,
        openai_error_message,
        probe_audio_duration,
        save_tts_audio,
    )
    from video_render_helpers import (
        TEXT_FIT_CHAR_WIDTH_FACTOR,
        bbox_to_pixels,
        dialogue_layout_from_box,
        escape_filter_path,
        fit_wrapped_text,
        mode_char_width_factor,
        narration_layout_from_box,
        resolve_font_file,
        resolve_narration_font_file,
        resolve_overlay_assets,
        resolve_shape_font,
        scaled_svg_size,
    )
except ModuleNotFoundError:
    from scripts.pipeline_common import ROOT, build_openai_client, load_dotenv_if_present, read_json, write_json
    from scripts.generate_scene_assets import (
        create_mock_audio,
        default_narrator_profile,
        normalize_tts_voice_id,
        normalized_voice_profile,
        openai_error_message,
        probe_audio_duration,
        save_tts_audio,
    )
    from scripts.video_render_helpers import (
        TEXT_FIT_CHAR_WIDTH_FACTOR,
        bbox_to_pixels,
        dialogue_layout_from_box,
        escape_filter_path,
        fit_wrapped_text,
        mode_char_width_factor,
        narration_layout_from_box,
        resolve_font_file,
        resolve_narration_font_file,
        resolve_overlay_assets,
        resolve_shape_font,
        scaled_svg_size,
    )


DEFAULT_SCENE_ASSETS_DIR = ROOT / "artifacts" / "scene_assets"
DEFAULT_RENDER_PLAN_DIR = ROOT / "artifacts" / "render_plan"
DEFAULT_AUDIO_EVENTS_DIR = ROOT / "artifacts" / "audio_events"
DEFAULT_VIDEOS_CLEAN_DIR = ROOT / "artifacts" / "videos" / "clean"
DEFAULT_VIDEOS_COMPOSITED_DIR = ROOT / "artifacts" / "videos" / "composited"
DEFAULT_VIDEOS_FINAL_DIR = ROOT / "artifacts" / "videos" / "final"
DEFAULT_SUBS_FINAL_DIR = ROOT / "artifacts" / "subtitles" / "final"
DEFAULT_OVERLAY_DIR = ROOT / "assets" / "video_overlays"
DEFAULT_CHARACTER_DIR = ROOT / "data" / "characters"
DEFAULT_CANVAS_W = 1080
DEFAULT_CANVAS_H = 1920
DEFAULT_MARGIN_PX = 24
DEFAULT_FADE_IN_S = 0.10
DEFAULT_FADE_OUT_S = 0.10
DEFAULT_CROSSFADE_S = 0.10
DEFAULT_DOTENV_PATH = ROOT / ".env"
TMP_DIR = ROOT / ".tmp" / "video_pipeline_v2"

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

LEGIBILITY_WPS = {
    "narration": 2.6,
    "dialogue": 2.3,
    "shout": 2.0,
}
LEGIBILITY_FLOORS = {
    "narration": 1.4,
    "dialogue": 1.2,
    "shout": 0.9,
}
OVERLAY_LIMITS = {
    "narration": {
        "min_w": 620,
        "max_w": 920,
        "min_h": 180,
        "max_h": 360,
        "min_font": 30,
        "max_font": 44,
        "max_lines": 3,
        "pad_x": 56,
        "pad_y": 34,
        "adaptive": False,
    },
    "dialogue": {
        "min_w": 460,
        "max_w": 800,
        "min_h": 170,
        "max_h": 340,
        "min_font": 24,
        "max_font": 40,
        "max_lines": 4,
        "pad_x": 36,
        "pad_y": 24,
        "adaptive": True,
    },
    "shout": {
        "min_w": 460,
        "max_w": 920,
        "min_h": 180,
        "max_h": 360,
        "min_font": 26,
        "max_font": 46,
        "max_lines": 4,
        "pad_x": 42,
        "pad_y": 28,
        "adaptive": True,
    },
}


@dataclass
class ScenePaths:
    events: Path
    utterances: Path
    alignment: Path
    audio_plan: Path
    overlay_timeline: Path
    camera_plan: Path
    audio_dir: Path
    clean_video: Path
    composited_video: Path


def load_json(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid JSON payload: {path}")
    return payload


def dump_json(path: Path, payload: dict[str, Any]) -> None:
    write_json(path, payload)


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def normalize_text(text: str) -> str:
    clean = normalize_ws(str(text or "").translate(SMART_PUNCT_TRANSLATION))
    clean = re.sub(r"\s+([,.;:!?])", r"\1", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    clean = re.sub(r"\s*\.\s*\.\s*\.", "...", clean)
    return clean


def continuation_display_text(text: str, page_index: int, page_count: int) -> str:
    clean = normalize_text(text)
    if not clean or page_count <= 1:
        return clean

    display = clean
    if page_index > 1 and not display.startswith("..."):
        display = display.lstrip(".").strip()
        display = f"...{display}"
    if page_index < page_count and not display.endswith("..."):
        display = display.rstrip(".").strip()
        display = f"{display}..."
    return display


def postproduction_repair_level() -> int:
    try:
        return max(0, int(os.environ.get("VIDEO_POSTPROD_REPAIR_LEVEL", "0")))
    except ValueError:
        return 0


def overlay_char_width_factor(kind: str, delivery: str = "normal") -> float:
    repair_bias = 1.0 + (0.035 * postproduction_repair_level())
    return mode_char_width_factor(kind, delivery=delivery) * repair_bias


def normalize_id(value: str) -> str:
    clean = normalize_text(value).lower().replace(" ", "_")
    return re.sub(r"[^a-z0-9_]+", "_", clean).strip("_")


def tokenize_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+(?:['-][A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+)*", normalize_text(text))


def count_words(text: str) -> int:
    return len(tokenize_words(text))


def punctuation_delay(text: str) -> float:
    clean = normalize_text(text)
    commas = len(re.findall(r"[,;:]", clean))
    sentence_ends = len(re.findall(r"[.!?]", clean))
    ellipsis = clean.count("...")
    return (commas * 0.12) + (sentence_ends * 0.22) + (ellipsis * 0.30)


def legibility_duration(text: str, kind: str, *, fade_in_s: float = 0.10, fade_out_s: float = 0.10) -> float:
    words = count_words(text)
    speed = LEGIBILITY_WPS.get(kind, LEGIBILITY_WPS["dialogue"])
    floor = LEGIBILITY_FLOORS.get(kind, LEGIBILITY_FLOORS["dialogue"])
    estimate = (words / max(0.1, speed)) + punctuation_delay(text) + fade_in_s + fade_out_s
    return max(floor, estimate)


def ensure_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found. Install ffmpeg first.")
    return ffmpeg


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_manifest_for_episode(episode_path: Path, assets_dir: Path = DEFAULT_SCENE_ASSETS_DIR) -> Path:
    episode = load_json(episode_path)
    episode_id = str(episode.get("episode_id", "")).strip()
    if not episode_id:
        raise RuntimeError(f"Episode file has no episode_id: {episode_path}")
    manifest_path = assets_dir / episode_id / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"Missing scene asset manifest: {manifest_path}")
    return manifest_path


def scene_paths(episode_id: str, scene_index: int) -> ScenePaths:
    base = DEFAULT_RENDER_PLAN_DIR / episode_id
    scene_name = f"scene_{scene_index:02d}"
    audio_dir = DEFAULT_AUDIO_EVENTS_DIR / episode_id / scene_name
    return ScenePaths(
        events=base / f"{scene_name}.events.json",
        utterances=base / f"{scene_name}.utterances.json",
        alignment=base / f"{scene_name}.alignment.json",
        audio_plan=base / f"{scene_name}.audio_plan.json",
        overlay_timeline=base / f"{scene_name}.overlay_timeline.json",
        camera_plan=base / f"{scene_name}.camera_plan.json",
        audio_dir=audio_dir,
        clean_video=DEFAULT_VIDEOS_CLEAN_DIR / episode_id / f"{scene_name}.clean.mp4",
        composited_video=DEFAULT_VIDEOS_COMPOSITED_DIR / episode_id / f"{scene_name}.composited.mp4",
    )


def iter_episode_and_manifest_scenes(episode_path: Path, manifest_path: Path) -> tuple[dict[str, Any], list[tuple[dict[str, Any], dict[str, Any]]]]:
    episode = load_json(episode_path)
    manifest = load_json(manifest_path)
    episode_scenes = episode.get("scenes")
    manifest_scenes = manifest.get("scenes")
    if not isinstance(episode_scenes, list) or not isinstance(manifest_scenes, list):
        raise RuntimeError("Episode or manifest has no scenes list.")
    if len(episode_scenes) != len(manifest_scenes):
        raise RuntimeError("Episode scenes do not match manifest scenes.")
    return episode, list(zip(episode_scenes, manifest_scenes))


def event_kind_for_phase(phase: dict[str, Any]) -> str:
    base_kind = normalize_ws(str(phase.get("phase_kind", "")).lower()) or "narration"
    if base_kind == "dialogue":
        delivery = normalize_ws(str(phase.get("dialogue_delivery", "")).lower())
        if delivery == "shout":
            return "shout"
        return "dialogue"
    return "narration"


def event_group_id(phase: dict[str, Any], kind: str) -> str:
    paragraph_index = int(phase.get("paragraph_index", 0))
    if kind == "narration":
        return f"narration:{paragraph_index}"
    speaker = normalize_id(str(phase.get("dialogue_speaker", "")) or str(phase.get("primary_actor", "")) or "speaker")
    return f"{kind}:{speaker}:{paragraph_index}"


def build_scene_events_payload(episode_path: Path, manifest_path: Path) -> list[dict[str, Any]]:
    episode, scenes = iter_episode_and_manifest_scenes(episode_path, manifest_path)
    episode_id = str(episode["episode_id"])
    payloads: list[dict[str, Any]] = []
    for scene, manifest_scene in scenes:
        idx = int(scene["scene_index"])
        phases = manifest_scene.get("text_phases") or manifest_scene.get("caption_blocks") or []
        if not isinstance(phases, list) or not phases:
            raise RuntimeError(f"Scene {idx} has no text phases in manifest.")
        events: list[dict[str, Any]] = []
        for order, phase in enumerate(phases, start=1):
            kind = event_kind_for_phase(phase)
            speaker = normalize_id(str(phase.get("dialogue_speaker", "")))
            page_index = int(phase.get("paragraph_block_index", order - 1)) + 1
            page_count = int(phase.get("paragraph_blocks_total", 1))
            text = continuation_display_text(str(phase.get("dialogue_line") or phase.get("text") or ""), page_index, page_count)
            audio_text = normalize_text(str(phase.get("audio_text") or phase.get("dialogue_line") or phase.get("text") or ""))
            if not text:
                continue
            group_id = event_group_id(phase, kind)
            events.append(
                {
                    "event_id": f"scene_{idx:02d}_ev_{order:03d}",
                    "order": order,
                    "kind": kind,
                    "speaker": speaker,
                    "delivery": normalize_ws(str(phase.get("dialogue_delivery", "normal")).lower()) or "normal",
                    "text": text,
                    "audio_text": audio_text,
                    "page_index": page_index,
                    "page_count_in_group": page_count,
                    "group_id": group_id,
                    "primary_actor": normalize_id(str(phase.get("primary_actor", ""))),
                    "focus_bbox": list(phase.get("focus_bbox", [])) if isinstance(phase.get("focus_bbox"), list) else [],
                    "overlay_candidate_bbox": list(phase.get("overlay_bbox", [])) if isinstance(phase.get("overlay_bbox"), list) else [],
                    "source_phase_index": int(phase.get("phase_index", order)),
                    "source_image_path": str(phase.get("image_path", manifest_scene.get("scene_image_path", ""))),
                    "source_prompt_path": str(phase.get("prompt_path", manifest_scene.get("prompt_path", ""))),
                    "requires_pre_roll_clean": None if kind == "dialogue" else False,
                }
            )
        payloads.append(
            {
                "episode_id": episode_id,
                "scene_index": idx,
                "source_scene_image_path": str(manifest_scene.get("scene_image_path") or manifest_scene.get("image_path") or ""),
                "initial_scene_estimate_s": float(scene.get("estimated_seconds", manifest_scene.get("estimated_seconds", 1))),
                "events": events,
            }
        )
    return payloads


def load_character_voice_profiles(character_dir: Path = DEFAULT_CHARACTER_DIR) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    for path in character_dir.glob("*.json"):
        try:
            payload = load_json(path)
        except Exception:
            continue
        char_id = normalize_id(str(payload.get("character_id", "")))
        voice_profile = payload.get("voice_profile")
        if char_id and isinstance(voice_profile, dict):
            profiles[char_id] = normalized_voice_profile(voice_profile)
    return profiles


def build_scene_utterances_payload(events_path: Path, character_dir: Path = DEFAULT_CHARACTER_DIR) -> dict[str, Any]:
    payload = load_json(events_path)
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        raise RuntimeError(f"No events found in {events_path}")
    character_profiles = load_character_voice_profiles(character_dir)
    narrator_profile = default_narrator_profile()
    utterances: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for event in events:
        if not isinstance(event, dict):
            continue
        group_id = str(event.get("group_id", ""))
        kind = str(event.get("kind", "dialogue"))
        speaker = normalize_id(str(event.get("speaker", "")))
        start_new = (
            current is None
            or current["kind"] != kind
            or current["group_id"] != group_id
            or current["speaker"] != speaker
        )
        if start_new:
            if current is not None:
                utterances.append(current)
            voice_profile = character_profiles.get(speaker, narrator_profile)
            current = {
                "utterance_id": f"utt_{len(utterances) + 1:03d}",
                "kind": kind,
                "speaker": speaker if kind != "narration" else "narrator",
                "delivery": normalize_ws(str(event.get("delivery", "normal")).lower()) or "normal",
                "full_text_parts": [],
                "event_ids": [],
                "tts_voice": normalize_tts_voice_id(str(voice_profile.get("tts_voice", narrator_profile["tts_voice"]))),
                "voice_profile": voice_profile,
                "group_id": group_id,
            }
        assert current is not None
        current["full_text_parts"].append(str(event.get("audio_text") or event.get("text") or ""))
        current["event_ids"].append(str(event.get("event_id")))
    if current is not None:
        utterances.append(current)

    for item in utterances:
        item["full_text"] = normalize_text(" ".join(item.pop("full_text_parts")))
        item.pop("group_id", None)

    return {
        "episode_id": payload["episode_id"],
        "scene_index": payload["scene_index"],
        "utterances": utterances,
    }


def openai_client_or_none(dotenv_path: Path, *, mock: bool) -> Any | None:
    if mock:
        return None
    load_dotenv_if_present(dotenv_path)
    return build_openai_client(dotenv_path)


def transcription_words_from_response(response: Any) -> list[dict[str, Any]]:
    if response is None:
        return []
    if isinstance(response, dict):
        if isinstance(response.get("words"), list):
            return [dict(item) for item in response["words"] if isinstance(item, dict)]
        segments = response.get("segments")
        if isinstance(segments, list):
            out: list[dict[str, Any]] = []
            for segment in segments:
                words = segment.get("words")
                if isinstance(words, list):
                    out.extend(dict(item) for item in words if isinstance(item, dict))
            return out
        return []
    words = getattr(response, "words", None)
    if isinstance(words, list):
        return [word.model_dump() if hasattr(word, "model_dump") else dict(word) for word in words]
    segments = getattr(response, "segments", None)
    if isinstance(segments, list):
        out = []
        for segment in segments:
            seg_words = getattr(segment, "words", None)
            if isinstance(seg_words, list):
                for word in seg_words:
                    if hasattr(word, "model_dump"):
                        out.append(word.model_dump())
                    elif isinstance(word, dict):
                        out.append(dict(word))
        return out
    return []


def estimate_alignment_from_text(text: str, duration_s: float) -> list[dict[str, Any]]:
    tokens = tokenize_words(text)
    if not tokens:
        return []
    step = max(0.04, float(duration_s) / max(1, len(tokens)))
    current = 0.0
    words: list[dict[str, Any]] = []
    for idx, token in enumerate(tokens):
        start = current
        end = duration_s if idx == len(tokens) - 1 else min(duration_s, start + step)
        words.append({"index": idx, "text": token, "start_s": round(start, 4), "end_s": round(end, 4)})
        current = end
    return words


def synthesize_scene_audio_payload(
    utterances_path: Path,
    audio_dir: Path,
    *,
    mock: bool,
    dotenv_path: Path = DEFAULT_DOTENV_PATH,
    tts_model: str | None = None,
) -> dict[str, Any]:
    payload = load_json(utterances_path)
    utterances = payload.get("utterances")
    if not isinstance(utterances, list):
        raise RuntimeError(f"Invalid utterances file: {utterances_path}")
    ffmpeg = ensure_ffmpeg()
    ensure_dir(audio_dir)
    client = openai_client_or_none(dotenv_path, mock=mock)
    model = tts_model or os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
    output_items: list[dict[str, Any]] = []
    for item in utterances:
        if not isinstance(item, dict):
            continue
        utterance_id = str(item["utterance_id"])
        output_audio = audio_dir / f"{utterance_id}.mp3"
        full_text = str(item.get("full_text", "")).strip()
        voice_profile = normalized_voice_profile(item.get("voice_profile"))
        if mock:
            words_per_second = float(voice_profile.get("words_per_second", default_narrator_profile()["words_per_second"]))
            estimated = max(1, math.ceil(max(1, count_words(full_text)) / max(0.2, words_per_second)))
            create_mock_audio(ffmpeg, output_audio, estimated)
        else:
            assert client is not None
            try:
                save_tts_audio(
                    client,
                    model=model,
                    voice=str(item.get("tts_voice") or voice_profile.get("tts_voice") or default_narrator_profile()["tts_voice"]),
                    narration=full_text,
                    output_audio=output_audio,
                    instructions=str(voice_profile.get("tone", "")) or None,
                )
            except Exception as exc:
                raise RuntimeError(openai_error_message(exc)) from exc
        output_items.append(
            {
                "utterance_id": utterance_id,
                "audio_clip_path": output_audio.as_posix(),
                "audio_duration_s": round(probe_audio_duration(ffmpeg, output_audio), 4),
            }
        )
    return {
        "episode_id": payload["episode_id"],
        "scene_index": payload["scene_index"],
        "utterances": output_items,
    }


def align_scene_audio_payload(
    utterances_path: Path,
    synthesized_audio_payload: dict[str, Any],
    *,
    mock: bool,
    dotenv_path: Path = DEFAULT_DOTENV_PATH,
    transcription_model: str | None = None,
    allow_estimated_fallback: bool = False,
) -> dict[str, Any]:
    utterances_payload = load_json(utterances_path)
    utterances = utterances_payload.get("utterances")
    if not isinstance(utterances, list):
        raise RuntimeError(f"Invalid utterances file: {utterances_path}")
    by_id = {str(item["utterance_id"]): item for item in utterances if isinstance(item, dict)}
    client = openai_client_or_none(dotenv_path, mock=mock)
    model = transcription_model or os.getenv("OPENAI_TRANSCRIPTION_MODEL", "whisper-1")
    out_utterances: list[dict[str, Any]] = []
    for audio_item in synthesized_audio_payload.get("utterances") or []:
        if not isinstance(audio_item, dict):
            continue
        utterance_id = str(audio_item["utterance_id"])
        utterance = by_id.get(utterance_id)
        if not utterance:
            continue
        audio_path = Path(str(audio_item["audio_clip_path"]))
        duration_s = float(audio_item.get("audio_duration_s", 0))
        words: list[dict[str, Any]]
        method = "estimated"
        if mock:
            words = estimate_alignment_from_text(str(utterance.get("full_text", "")), duration_s)
        else:
            assert client is not None
            try:
                with audio_path.open("rb") as handle:
                    response = client.audio.transcriptions.create(
                        model=model,
                        file=handle,
                        response_format="verbose_json",
                        timestamp_granularities=["word"],
                    )
                raw_words = transcription_words_from_response(response)
                words = [
                    {
                        "index": idx,
                        "text": normalize_text(str(word.get("word") or word.get("text") or "")),
                        "start_s": round(float(word.get("start", word.get("start_s", 0.0))), 4),
                        "end_s": round(float(word.get("end", word.get("end_s", 0.0))), 4),
                    }
                    for idx, word in enumerate(raw_words)
                    if normalize_text(str(word.get("word") or word.get("text") or ""))
                ]
                method = "openai"
                if not words:
                    raise RuntimeError("transcription returned no word-level timestamps")
            except Exception as exc:
                if not allow_estimated_fallback:
                    raise RuntimeError(openai_error_message(exc)) from exc
                words = estimate_alignment_from_text(str(utterance.get("full_text", "")), duration_s)
                method = "estimated_fallback"
        out_utterances.append(
            {
                "utterance_id": utterance_id,
                "audio_clip_path": audio_path.as_posix(),
                "alignment_method": method,
                "words": words,
            }
        )
    return {
        "episode_id": utterances_payload["episode_id"],
        "scene_index": utterances_payload["scene_index"],
        "utterances": out_utterances,
    }


def normalize_alignment_token(token: str) -> str:
    clean = normalize_text(token).lower()
    clean = unicodedata.normalize("NFKD", clean)
    clean = "".join(char for char in clean if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", clean)


ALIGNMENT_CONTRACTIONS = {
    "del": ("de", "el"),
    "al": ("a", "el"),
}


def match_alignment_token(
    word_tokens: list[str],
    word_idx: int,
    target_tokens: list[str],
    target_idx: int,
) -> tuple[int, int] | None:
    if word_idx >= len(word_tokens) or target_idx >= len(target_tokens):
        return None
    word_token = word_tokens[word_idx]
    target_token = target_tokens[target_idx]
    if word_token == target_token:
        return word_idx + 1, target_idx + 1

    if word_token.isdigit() and target_token.isdigit() and word_idx + 1 < len(word_tokens):
        merged_word = word_token + word_tokens[word_idx + 1]
        if merged_word == target_token:
            return word_idx + 2, target_idx + 1

    if word_token.isdigit() and target_token.isdigit() and target_idx + 1 < len(target_tokens):
        merged_target = target_token + target_tokens[target_idx + 1]
        if merged_target == word_token:
            return word_idx + 1, target_idx + 2

    target_expansion = ALIGNMENT_CONTRACTIONS.get(target_token)
    if target_expansion and word_idx + 1 < len(word_tokens):
        if (word_tokens[word_idx], word_tokens[word_idx + 1]) == target_expansion:
            return word_idx + 2, target_idx + 1

    word_expansion = ALIGNMENT_CONTRACTIONS.get(word_token)
    if word_expansion and target_idx + 1 < len(target_tokens):
        if tuple(target_tokens[target_idx : target_idx + 2]) == word_expansion:
            return word_idx + 1, target_idx + 2

    if (
        len(word_token) >= 5
        and len(target_token) >= 5
        and SequenceMatcher(None, word_token, target_token).ratio() >= 0.78
        and (
            word_token[0] == target_token[0]
            or word_token[:4] == target_token[:4]
            or word_token[-3:] == target_token[-3:]
        )
    ):
        return word_idx + 1, target_idx + 1

    return None


def approximate_alignment_span(word_tokens: list[str], target_tokens: list[str], cursor: int) -> tuple[int, int] | None:
    if not word_tokens or not target_tokens or cursor >= len(word_tokens):
        return None

    best_ratio = -1.0
    best_span: tuple[int, int] | None = None
    max_extra_words = max(2, len(target_tokens) // 4)

    for start_idx in range(max(0, cursor), len(word_tokens)):
        end_limit = min(len(word_tokens), start_idx + len(target_tokens) + max_extra_words)
        for end_idx in range(start_idx + 1, end_limit + 1):
            window = word_tokens[start_idx:end_idx]
            if not window:
                continue
            ratio = SequenceMatcher(None, window, target_tokens).ratio()
            if window[0] == target_tokens[0]:
                ratio += 0.08
            if window[-1] == target_tokens[-1]:
                ratio += 0.08
            if len(window) == len(target_tokens):
                ratio += 0.04
            if ratio > best_ratio:
                best_ratio = ratio
                best_span = (start_idx, end_idx - 1)

    if best_span and best_ratio >= 0.58:
        return best_span
    return None


def extract_word_timings_for_event(event: dict[str, Any], utterance_alignment: dict[str, Any], start_cursor: int) -> tuple[int, int]:
    words = utterance_alignment.get("words") or []
    word_tokens = [normalize_alignment_token(str(item.get("text", ""))) for item in words]
    target_tokens = [normalize_alignment_token(item) for item in tokenize_words(str(event.get("audio_text") or event.get("text") or ""))]
    if not target_tokens:
        if start_cursor < len(words):
            return start_cursor, start_cursor
        return 0, 0
    cursor = max(0, start_cursor)
    start_idx: int | None = None
    last_idx: int | None = None
    target_idx = 0
    while target_idx < len(target_tokens):
        found = None
        for idx in range(cursor, len(word_tokens)):
            matched = match_alignment_token(word_tokens, idx, target_tokens, target_idx)
            if matched is not None:
                next_cursor, next_target_idx = matched
                consumed_word_end = next_cursor - 1
                found = idx
                cursor = next_cursor
                target_idx = next_target_idx
                last_idx = consumed_word_end
                break
        if found is None:
            fallback = approximate_alignment_span(word_tokens, target_tokens, cursor)
            if fallback is not None:
                return fallback
            remaining_targets = len(target_tokens) - target_idx
            matched_any = start_idx is not None and last_idx is not None
            remaining_words = len(word_tokens) - cursor
            if matched_any and remaining_words == 0:
                break
            if matched_any and remaining_targets <= 2 and remaining_words <= 1 and len(target_tokens) >= 4:
                break
            raise RuntimeError(f"Unable to align event '{event.get('event_id')}' inside utterance '{utterance_alignment.get('utterance_id')}'.")
        if start_idx is None:
            start_idx = found
    assert start_idx is not None and last_idx is not None
    return start_idx, last_idx


def bbox_intersection(a: list[float], b: list[float]) -> float:
    if len(a) != 4 or len(b) != 4:
        return 0.0
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    h = max(0.0, min(ay2, by2) - max(ay1, by1))
    return w * h


def bbox_area(bbox: list[float]) -> float:
    if len(bbox) != 4:
        return 0.0
    return max(0.0, float(bbox[2])) * max(0.0, float(bbox[3]))


def region_is_face_like(region: dict[str, Any]) -> bool:
    label = normalize_ws(str(region.get("label", ""))).lower()
    kind = normalize_ws(str(region.get("kind", ""))).lower()
    blob = f"{label} {kind}"
    return any(token in blob for token in ("face", "rostro", "rostros", "cara", "caras", "ojo", "ojos", "eye", "eyes", "mouth", "boca"))


def region_is_hand_like(region: dict[str, Any]) -> bool:
    label = normalize_ws(str(region.get("label", ""))).lower()
    kind = normalize_ws(str(region.get("kind", ""))).lower()
    blob = f"{label} {kind}"
    return any(token in blob for token in ("hand", "hands", "mano", "manos"))


def speaker_matches_region(speaker: str, label: str) -> bool:
    speaker_id = normalize_id(speaker)
    label_id = normalize_id(label)
    return bool(speaker_id and speaker_id in label_id)


def analyze_occlusion(scene_layout: dict[str, Any], event: dict[str, Any], box_norm: list[float]) -> dict[str, Any]:
    focus_bbox = []
    if isinstance(event.get("focus_bbox"), list):
        focus_bbox = list(event.get("focus_bbox", []))
    if not focus_bbox:
        camera_track = scene_layout.get("camera_track") or {}
        if isinstance(camera_track.get("focus_bbox"), list):
            focus_bbox = list(camera_track["focus_bbox"])
    protected_regions = scene_layout.get("protected_regions") or []
    covers_focus = bbox_intersection(box_norm, focus_bbox) > max(0.01, bbox_area(focus_bbox) * 0.05) if focus_bbox else False
    covers_speaker = False
    covers_listener_face = False
    severity = 0.0
    speaker = str(event.get("speaker", ""))
    for region in protected_regions:
        if not isinstance(region, dict):
            continue
        region_bbox = list(region.get("bbox", [])) if isinstance(region.get("bbox"), list) else []
        if len(region_bbox) != 4:
            continue
        overlap = bbox_intersection(box_norm, region_bbox)
        if overlap <= 0:
            continue
        importance = float(region.get("importance", 0.0))
        overlap_ratio = overlap / max(0.0001, bbox_area(region_bbox))
        label = str(region.get("label", ""))
        face_like = region_is_face_like(region)
        if speaker and speaker_matches_region(speaker, label):
            covers_speaker = overlap_ratio > 0.05
        elif face_like and overlap_ratio > 0.03 and importance >= 0.75:
            covers_listener_face = True
            severity = max(severity, overlap_ratio)
    requires_pre_roll = bool(
        event.get("kind") == "dialogue"
        and covers_listener_face
        and not covers_speaker
        and not covers_focus
    )
    if severity >= 0.35:
        pre_roll = 0.9
    elif severity >= 0.18:
        pre_roll = 0.6
    elif severity > 0:
        pre_roll = 0.4
    else:
        pre_roll = 0.0
    return {
        "covers_speaker": covers_speaker,
        "covers_focus": covers_focus,
        "covers_listener_face": covers_listener_face,
        "requires_pre_roll_clean": requires_pre_roll,
        "recommended_pre_roll_s": pre_roll if requires_pre_roll else 0.0,
    }


def build_scene_audio_plan_payload(events_path: Path, utterances_path: Path, alignment_path: Path, manifest_path: Path) -> dict[str, Any]:
    events_payload = load_json(events_path)
    utterances_payload = load_json(utterances_path)
    alignment_payload = load_json(alignment_path)
    manifest = load_json(manifest_path)
    scene_index = int(events_payload["scene_index"])
    manifest_scene = (manifest.get("scenes") or [])[scene_index - 1]
    scene_layout = dict(manifest_scene.get("layout_analysis") or {})

    events = [item for item in events_payload.get("events", []) if isinstance(item, dict)]
    utterances = [item for item in utterances_payload.get("utterances", []) if isinstance(item, dict)]
    alignments = {
        str(item["utterance_id"]): item
        for item in alignment_payload.get("utterances", [])
        if isinstance(item, dict)
    }
    event_by_id = {str(item["event_id"]): item for item in events}

    scene_cursor = 0.0
    audio_items: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    last_narration_visible_end = 0.0
    last_event_kind = ""

    for utterance in utterances:
        utterance_id = str(utterance["utterance_id"])
        alignment = alignments.get(utterance_id)
        if not alignment:
            raise RuntimeError(f"Missing alignment for {utterance_id}")
        words = alignment.get("words") or []
        if not words:
            raise RuntimeError(f"Alignment has no words for {utterance_id}")
        clip_path = Path(str(alignment.get("audio_clip_path", "")))
        clip_duration = probe_audio_duration(ensure_ffmpeg(), clip_path)
        utterance_events = [event_by_id[event_id] for event_id in utterance.get("event_ids", []) if event_id in event_by_id]
        if not utterance_events:
            continue
        first_event = utterance_events[0]
        occlusion = analyze_occlusion(scene_layout, first_event, list(first_event.get("overlay_candidate_bbox", [])))
        pre_roll_clean_s = float(occlusion["recommended_pre_roll_s"])
        scene_audio_start_s = scene_cursor + pre_roll_clean_s
        scene_audio_end_s = scene_audio_start_s + clip_duration
        audio_items.append(
            {
                "utterance_id": utterance_id,
                "audio_clip_path": clip_path.as_posix(),
                "audio_duration_s": round(clip_duration, 4),
                "scene_audio_start_s": round(scene_audio_start_s, 4),
                "scene_audio_end_s": round(scene_audio_end_s, 4),
            }
        )
        word_cursor = 0
        event_ranges: list[tuple[dict[str, Any], int, int]] = []
        for event in utterance_events:
            start_idx, end_idx = extract_word_timings_for_event(event, alignment, word_cursor)
            event_ranges.append((event, start_idx, end_idx))
            word_cursor = end_idx + 1

        for idx, (event, start_idx, end_idx) in enumerate(event_ranges):
            speech_start_s = scene_audio_start_s + float(words[start_idx]["start_s"])
            speech_end_s = scene_audio_start_s + float(words[end_idx]["end_s"])
            if idx + 1 < len(event_ranges):
                next_start_idx = event_ranges[idx + 1][1]
                associated_end_s = scene_audio_start_s + float(words[next_start_idx]["start_s"])
                associated_end_s = max(associated_end_s, speech_end_s)
            else:
                associated_end_s = scene_audio_end_s
            min_legible = legibility_duration(str(event.get("text", "")), str(event.get("kind", "dialogue")))
            if str(event.get("kind", "dialogue")) == "narration" and last_event_kind == "narration":
                visible_start_s = min(last_narration_visible_end, speech_start_s)
            else:
                visible_start_s = speech_start_s
            visible_end_s = max(associated_end_s, visible_start_s + min_legible)
            bindings.append(
                {
                    "event_id": str(event["event_id"]),
                    "utterance_id": utterance_id,
                    "speech_start_s": round(speech_start_s, 4),
                    "speech_end_s": round(speech_end_s, 4),
                    "visible_start_s": round(visible_start_s, 4),
                    "visible_end_s": round(visible_end_s, 4),
                    "audio_start_s": round(scene_audio_start_s, 4),
                    "audio_end_s": round(scene_audio_end_s, 4),
                    "pre_roll_clean_s": round(pre_roll_clean_s if idx == 0 and event.get("kind") == "dialogue" and occlusion["requires_pre_roll_clean"] else 0.0, 4),
                    "legibility_floor_s": round(min_legible, 4),
                }
            )
            if str(event.get("kind", "dialogue")) == "narration":
                last_narration_visible_end = visible_end_s
            last_event_kind = str(event.get("kind", "dialogue"))
        scene_cursor = max(scene_audio_end_s, max((binding["visible_end_s"] for binding in bindings if binding["utterance_id"] == utterance_id), default=scene_audio_end_s))

    return {
        "episode_id": events_payload["episode_id"],
        "scene_index": scene_index,
        "scene_duration_final_s": round(scene_cursor, 4),
        "utterances": audio_items,
        "event_bindings": bindings,
    }


def build_piecewise_linear_expr(keyframes: list[dict[str, Any]], value_key: str) -> str:
    if not keyframes:
        return "0"
    if len(keyframes) == 1:
        return f"{float(keyframes[0][value_key]):.6f}"
    expr = f"{float(keyframes[-1][value_key]):.6f}"
    for idx in range(len(keyframes) - 2, -1, -1):
        left = keyframes[idx]
        right = keyframes[idx + 1]
        t0 = float(left["time_s"])
        t1 = float(right["time_s"])
        v0 = float(left[value_key])
        v1 = float(right[value_key])
        if t1 <= t0:
            segment_expr = f"{v1:.6f}"
        else:
            slope = (v1 - v0) / (t1 - t0)
            segment_expr = f"({v0:.6f}+((t-{t0:.6f})*{slope:.6f}))"
        expr = f"if(lte(t,{t1:.6f}),{segment_expr},{expr})"
    return expr


def scene_layout_bounds(scene_kind: str) -> dict[str, Any]:
    return OVERLAY_LIMITS.get(scene_kind, OVERLAY_LIMITS["dialogue"])


def adaptive_overlay_size(kind: str, text: str, candidate_bbox: list[float]) -> tuple[int, int]:
    limits = scene_layout_bounds(kind)
    words = max(1, count_words(text))
    candidate_w = max(1, round(candidate_bbox[2] * DEFAULT_CANVAS_W)) if len(candidate_bbox) == 4 else limits["max_w"]
    candidate_h = max(1, round(candidate_bbox[3] * DEFAULT_CANVAS_H)) if len(candidate_bbox) == 4 else limits["max_h"]
    if not limits["adaptive"]:
        return (
            max(limits["min_w"], min(limits["max_w"], candidate_w)),
            max(limits["min_h"], min(limits["max_h"], candidate_h)),
        )
    if kind == "shout":
        scale = 0.56 if words <= 4 else 0.72 if words <= 10 else 0.90
    else:
        scale = 0.56 if words <= 4 else 0.70 if words <= 10 else 0.88
    target_w = max(limits["min_w"], min(limits["max_w"], round(candidate_w * scale)))
    estimated_lines = max(1, min(limits["max_lines"], math.ceil(words / 5.5)))
    base_h = limits["min_h"] + ((estimated_lines - 1) * 54)
    height_scale = 0.66 if words <= 6 else 0.84
    if kind == "shout":
        height_scale = 0.68 if words <= 6 else 0.86
    target_h = max(limits["min_h"], min(limits["max_h"], max(base_h, round(candidate_h * height_scale))))
    return target_w, target_h


def centered_box_from_candidate(candidate_bbox: list[float], width_px: int, height_px: int) -> list[float]:
    if len(candidate_bbox) == 4:
        cx = float(candidate_bbox[0]) + (float(candidate_bbox[2]) / 2)
        cy = float(candidate_bbox[1]) + (float(candidate_bbox[3]) / 2)
    else:
        cx = 0.5
        cy = 0.5
    width_norm = width_px / DEFAULT_CANVAS_W
    height_norm = height_px / DEFAULT_CANVAS_H
    x = min(max(0.0, cx - (width_norm / 2)), 1.0 - width_norm)
    y = min(max(0.0, cy - (height_norm / 2)), 1.0 - height_norm)
    return [round(x, 4), round(y, 4), round(width_norm, 4), round(height_norm, 4)]


def clamp_overlay_box(box: list[float]) -> list[float]:
    if len(box) != 4:
        return [0.0, 0.0, 1.0, 1.0]
    w = min(max(float(box[2]), 0.02), 1.0)
    h = min(max(float(box[3]), 0.02), 1.0)
    x = min(max(0.0, float(box[0])), 1.0 - w)
    y = min(max(0.0, float(box[1])), 1.0 - h)
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


def bbox_center(box: list[float]) -> tuple[float, float]:
    if len(box) != 4:
        return 0.5, 0.5
    return float(box[0]) + (float(box[2]) / 2), float(box[1]) + (float(box[3]) / 2)


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def candidate_overlay_positions(kind: str, candidate_bbox: list[float], *, width_px: int, height_px: int) -> list[list[float]]:
    width_norm = width_px / DEFAULT_CANVAS_W
    height_norm = height_px / DEFAULT_CANVAS_H
    positions: list[list[float]] = []

    def add_pos(cx: float, cy: float) -> None:
        x = min(max(0.0, cx - (width_norm / 2)), 1.0 - width_norm)
        y = min(max(0.0, cy - (height_norm / 2)), 1.0 - height_norm)
        positions.append([round(x, 4), round(y, 4), round(width_norm, 4), round(height_norm, 4)])

    if len(candidate_bbox) == 4:
        cand_cx, cand_cy = bbox_center(candidate_bbox)
        cand_w = float(candidate_bbox[2])
        cand_h = float(candidate_bbox[3])
        for dx in (-0.48, -0.3, -0.16, 0.0, 0.16, 0.3, 0.48):
            for dy in (-0.48, -0.3, -0.16, 0.0, 0.16, 0.3, 0.48):
                add_pos(cand_cx + (dx * cand_w), cand_cy + (dy * cand_h))

    if kind == "narration":
        preferred_y = [0.78, 0.62, 0.48, 0.28]
        preferred_x = [0.5]
    else:
        preferred_y = [0.10, 0.22, 0.34, 0.50, 0.68, 0.82]
        preferred_x = [0.12, 0.28, 0.50, 0.72, 0.88]
    for cy in preferred_y:
        for cx in preferred_x:
            add_pos(cx, cy)

    deduped: list[list[float]] = []
    seen: set[tuple[int, int, int, int]] = set()
    for item in positions:
        key = tuple(round(v * 1000) for v in item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def select_overlay_box(
    *,
    kind: str,
    candidate_bbox: list[float],
    protected_regions: list[dict[str, Any]],
    focus_bbox: list[float],
    used_boxes: list[list[float]],
    width_px: int,
    height_px: int,
) -> list[float]:
    focus_center = bbox_center(focus_bbox) if len(focus_bbox) == 4 else (0.5, 0.5)
    candidate_target = bbox_center(candidate_bbox) if len(candidate_bbox) == 4 else (0.5, 0.5)
    size_candidates: list[tuple[int, int]] = [(width_px, height_px)]
    if kind != "narration":
        size_candidates.extend(
            [
                (max(240, round(width_px * 0.88)), max(120, round(height_px * 0.88))),
                (max(220, round(width_px * 0.78)), max(110, round(height_px * 0.78))),
            ]
        )

    best_safe_score: float | None = None
    best_safe_box: list[float] | None = None
    best_fallback_score: float | None = None
    best_fallback_box: list[float] | None = None

    for size_w, size_h in size_candidates:
        candidates = candidate_overlay_positions(kind, candidate_bbox, width_px=size_w, height_px=size_h)
        if not candidates:
            continue
        for box in candidates:
            face_overlap = 0.0
            hand_overlap = 0.0
            occluded_regions = 0
            for region in protected_regions:
                if not isinstance(region, dict):
                    continue
                region_bbox = list(region.get("bbox", [])) if isinstance(region.get("bbox"), list) else []
                if len(region_bbox) != 4:
                    continue
                overlap = bbox_intersection(box, region_bbox)
                if overlap <= 0:
                    continue
                occluded_regions += 1
                importance = float(region.get("importance", 0.5))
                if region_is_face_like(region):
                    face_overlap += (overlap / max(0.0001, bbox_area(region_bbox))) * importance
                elif region_is_hand_like(region):
                    hand_overlap += (overlap / max(0.0001, bbox_area(region_bbox))) * importance
            score = 0.0
            if len(focus_bbox) == 4:
                score += bbox_intersection(box, focus_bbox) * 10.0
            for prev in used_boxes[-2:]:
                if len(prev) == 4:
                    score += bbox_intersection(box, prev) * 1.5
            score += distance(bbox_center(box), candidate_target) * 0.12
            if kind == "dialogue":
                score += abs(bbox_center(box)[1] - 0.34) * 0.08
            else:
                score += abs(bbox_center(box)[1] - 0.78) * 0.12
            score += distance(bbox_center(box), focus_center) * 0.04
            score += hand_overlap * 1.2
            score += occluded_regions * 0.06

            if face_overlap <= 0.0001:
                if best_safe_score is None or score < best_safe_score:
                    best_safe_score = score
                    best_safe_box = box
            else:
                fallback_score = score + (face_overlap * 40.0)
                if best_fallback_score is None or fallback_score < best_fallback_score:
                    best_fallback_score = fallback_score
                    best_fallback_box = box

    chosen = best_safe_box or best_fallback_box
    if chosen is None:
        chosen = centered_box_from_candidate(candidate_bbox, width_px, height_px)
    return clamp_overlay_box(chosen)


def apply_overlay_crossfades(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create technical overlap for contiguous pages without affecting the scene base."""

    if not events:
        return events
    adjusted = [dict(item) for item in events]
    for prev, nxt in zip(adjusted, adjusted[1:]):
        prev_end = float(prev["end_s"])
        next_start = float(nxt["start_s"])
        if abs(next_start - prev_end) > 1e-3:
            continue
        crossfade = min(
            float(prev.get("crossfade_text_s", DEFAULT_CROSSFADE_S)),
            float(nxt.get("crossfade_text_s", DEFAULT_CROSSFADE_S)),
            max(0.0, float(prev["end_s"]) - float(prev["start_s"])),
            max(0.0, float(nxt["end_s"]) - float(nxt["start_s"])),
        )
        if crossfade <= 0:
            continue
        half = crossfade / 2
        prev["end_s"] = round(prev_end + half, 4)
        nxt["start_s"] = round(max(0.0, next_start - half), 4)
    return adjusted


def build_scene_overlay_timeline_payload(events_path: Path, audio_plan_path: Path, manifest_path: Path) -> dict[str, Any]:
    events_payload = load_json(events_path)
    audio_plan = load_json(audio_plan_path)
    manifest = load_json(manifest_path)
    scene_index = int(events_payload["scene_index"])
    manifest_scene = (manifest.get("scenes") or [])[scene_index - 1]
    scene_layout = dict(manifest_scene.get("layout_analysis") or {})
    events = [item for item in events_payload.get("events", []) if isinstance(item, dict)]
    bindings = {str(item["event_id"]): item for item in audio_plan.get("event_bindings", []) if isinstance(item, dict)}
    narration_box: list[float] | None = None
    out_events: list[dict[str, Any]] = []
    for event in events:
        binding = bindings.get(str(event["event_id"]))
        if not binding:
            raise RuntimeError(f"Missing audio binding for event {event['event_id']}")
        kind = str(event.get("kind", "dialogue"))
        delivery = str(event.get("delivery", "normal"))
        display_text = continuation_display_text(
            str(event.get("text", "")),
            int(event.get("page_index", 1)),
            int(event.get("page_count_in_group", 1)),
        )
        candidate_bbox = list(event.get("overlay_candidate_bbox", []))
        if kind == "narration":
            if narration_box is None:
                narration_box = candidate_bbox if len(candidate_bbox) == 4 else [0.08, 0.68, 0.84, 0.24]
            final_box = narration_box
        else:
            overlay_w, overlay_h = adaptive_overlay_size(kind, display_text, candidate_bbox)
            final_box = select_overlay_box(
                kind=kind,
                candidate_bbox=candidate_bbox or [0.5, 0.2, 0.5, 0.2],
                protected_regions=list(scene_layout.get("protected_regions") or []),
                focus_bbox=list(event.get("focus_bbox", [])) if isinstance(event.get("focus_bbox"), list) else [],
                used_boxes=[list(item.get("region", {}).get("box_norm", [])) for item in out_events if isinstance(item.get("region", {}).get("box_norm", []), list)],
                width_px=overlay_w,
                height_px=overlay_h,
            )
        occlusion = analyze_occlusion(scene_layout, event, final_box)
        limits = scene_layout_bounds(kind)
        box_x, box_y, box_w, box_h = bbox_to_pixels(final_box, frame_w=DEFAULT_CANVAS_W, frame_h=DEFAULT_CANVAS_H, fallback_w=limits["max_w"], fallback_h=limits["max_h"])
        text_box_w = max(40, box_w - (2 * limits["pad_x"]))
        text_box_h = max(40, box_h - (2 * limits["pad_y"]))
        wrapped_text, font_px, _line_spacing = fit_wrapped_text(
            display_text,
            text_box_w,
            text_box_h,
            max_font_size=int(limits["max_font"]),
            min_font_size=int(limits["min_font"]),
            max_lines=int(limits["max_lines"]),
            char_width_factor=overlay_char_width_factor(kind, delivery=delivery),
        )
        line_count = max(1, wrapped_text.count("\n") + 1)
        tail_anchor_norm = []
        if len(event.get("focus_bbox", [])) == 4:
            focus_bbox = event["focus_bbox"]
            tail_anchor_norm = [round(float(focus_bbox[0]) + (float(focus_bbox[2]) / 2), 4), round(float(focus_bbox[1]) + (float(focus_bbox[3]) / 2), 4)]
        out_events.append(
            {
                "event_id": str(event["event_id"]),
                "kind": kind,
                "speaker": str(event.get("speaker", "")),
                "delivery": delivery,
                "text": display_text,
                "start_s": float(binding["visible_start_s"]),
                "end_s": float(binding["visible_end_s"]),
                "fade_in_s": 0.0,
                "fade_out_s": 0.0,
                "crossfade_text_s": 0.0,
                "pre_roll_clean_s": float(binding.get("pre_roll_clean_s", 0.0)),
                "region": {
                    "box_norm": final_box,
                    "tail_anchor_norm": tail_anchor_norm,
                    "size_mode": "adaptive" if limits["adaptive"] else "fixed",
                },
                "layout": {
                    "font_px": int(font_px),
                    "line_count": int(line_count),
                    "padding_x_px": int(limits["pad_x"]),
                    "padding_y_px": int(limits["pad_y"]),
                },
                "visibility_policy": {
                    "single_visible_unit": True,
                    "hide_others": True,
                },
                "occlusion_analysis": occlusion,
            }
        )
    out_events = apply_overlay_crossfades(out_events)
    return {
        "episode_id": events_payload["episode_id"],
        "scene_index": scene_index,
        "scene_duration_s": float(audio_plan["scene_duration_final_s"]),
        "canvas": {"width": DEFAULT_CANVAS_W, "height": DEFAULT_CANVAS_H},
        "source_image_path": str(manifest_scene.get("scene_image_path") or manifest_scene.get("image_path") or ""),
        "source_clean_video_path": scene_paths(str(events_payload["episode_id"]), scene_index).clean_video.as_posix(),
        "events": out_events,
    }


def choose_camera_center(target_c: float, half_extent: float, min_edge: float, max_edge: float) -> float:
    lower = max(half_extent, max_edge - half_extent)
    upper = min(1.0 - half_extent, min_edge + half_extent)
    if lower - upper > 1e-6:
        raise RuntimeError("No feasible camera center range for active overlay.")
    if lower > upper:
        midpoint = (lower + upper) / 2
        lower = midpoint
        upper = midpoint
    return min(max(target_c, lower), upper)


def max_zoom_for_box(box_norm: list[float], margin_px: int = DEFAULT_MARGIN_PX) -> float:
    if len(box_norm) != 4:
        return 1.14
    margin_x = margin_px / DEFAULT_CANVAS_W
    margin_y = margin_px / DEFAULT_CANVAS_H
    required_w = float(box_norm[2]) + (2 * margin_x)
    required_h = float(box_norm[3]) + (2 * margin_y)
    return min(1.14, 1.0 / max(required_w, required_h, 0.001))


def active_event_at_time(events: list[dict[str, Any]], time_s: float) -> dict[str, Any] | None:
    for event in events:
        if float(event["start_s"]) <= time_s < float(event["end_s"]):
            return event
    return None


def build_scene_camera_plan_payload(overlay_timeline_path: Path, manifest_path: Path) -> dict[str, Any]:
    overlay_timeline = load_json(overlay_timeline_path)
    manifest = load_json(manifest_path)
    scene_index = int(overlay_timeline["scene_index"])
    manifest_scene = (manifest.get("scenes") or [])[scene_index - 1]
    scene_duration = float(overlay_timeline["scene_duration_s"])
    events = [item for item in overlay_timeline.get("events", []) if isinstance(item, dict)]
    keyframes: list[dict[str, Any]] = [
        {
            "time_s": 0.0,
            "zoom": 1.0,
            "focus_x": 0.5,
            "focus_y": 0.5,
            "active_event_id": str(events[0]["event_id"]) if events else "",
        }
    ]
    return {
        "scene_index": scene_index,
        "duration_s": scene_duration,
        "mode": "static_frame",
        "keyframes": keyframes,
        "constraints": {
            "keep_active_overlay_inside_frame": False,
            "zoom_min": 1.0,
            "zoom_max": 1.0,
            "monotonic_zoom": False,
        },
    }


def ffmpeg_run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def create_scene_audio_mix(ffmpeg: str, audio_plan: dict[str, Any], output_audio: Path) -> None:
    utterances = [item for item in audio_plan.get("utterances", []) if isinstance(item, dict)]
    if not utterances:
        raise RuntimeError("audio_plan has no utterances to mix")
    ensure_dir(output_audio.parent)
    total_duration = float(audio_plan["scene_duration_final_s"])
    inputs: list[str] = []
    filter_lines = [
        f"anullsrc=channel_layout=mono:sample_rate=24000:d={total_duration:.4f}[basea]"
    ]
    mix_labels = ["[basea]"]
    for idx, utterance in enumerate(utterances, start=1):
        clip_path = str(utterance["audio_clip_path"])
        delay_ms = int(round(float(utterance["scene_audio_start_s"]) * 1000))
        inputs.extend(["-i", clip_path])
        label = f"a{idx}"
        filter_lines.append(f"[{idx - 1}:a]adelay={delay_ms}|{delay_ms},atrim=0:{total_duration:.4f}[{label}]")
        mix_labels.append(f"[{label}]")
    filter_lines.append(f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:normalize=0,atrim=0:{total_duration:.4f}[aout]")
    cmd = [
        ffmpeg,
        "-y",
        *inputs,
        "-filter_complex",
        ";".join(filter_lines),
        "-map",
        "[aout]",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        output_audio.as_posix(),
    ]
    ffmpeg_run(cmd)


def render_clean_scene_video(scene_events_path: Path, audio_plan_path: Path, manifest_path: Path, output_video: Path, *, width: int = DEFAULT_CANVAS_W, height: int = DEFAULT_CANVAS_H, fps: int = 30) -> None:
    ffmpeg = ensure_ffmpeg()
    events_payload = load_json(scene_events_path)
    audio_plan = load_json(audio_plan_path)
    manifest = load_json(manifest_path)
    scene_index = int(events_payload["scene_index"])
    manifest_scene = (manifest.get("scenes") or [])[scene_index - 1]
    image_path = Path(str(manifest_scene.get("scene_image_path") or manifest_scene.get("image_path") or ""))
    if not image_path.exists():
        raise RuntimeError(f"Missing scene image for clean render: {image_path}")
    scene_layout = dict(manifest_scene.get("layout_analysis") or {})
    center_x = 0.5
    center_y = 0.5
    audio_tmp = TMP_DIR / str(events_payload["episode_id"]) / f"scene_{scene_index:02d}.clean.audio.m4a"
    ensure_dir(audio_tmp.parent)
    create_scene_audio_mix(ffmpeg, audio_plan, audio_tmp)
    duration = float(audio_plan["scene_duration_final_s"])
    crop_x_expr = f"min(max(iw*{center_x:.6f}-{width}/2,0),iw-{width})"
    crop_y_expr = f"min(max(ih*{center_y:.6f}-{height}/2,0),ih-{height})"
    vf = ",".join(
        [
            f"scale={width}:{height}:force_original_aspect_ratio=increase",
            f"crop={width}:{height}:x='{crop_x_expr}':y='{crop_y_expr}'",
            "eq=contrast=1.08:saturation=1.36:brightness=0.01",
            "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.10:t=22",
            "format=yuv420p",
        ]
    )
    ensure_dir(output_video.parent)
    cmd = [
        ffmpeg,
        "-y",
        "-loop",
        "1",
        "-i",
        image_path.as_posix(),
        "-i",
        audio_tmp.as_posix(),
        "-t",
        f"{duration:.4f}",
        "-vf",
        vf,
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        output_video.as_posix(),
    ]
    ffmpeg_run(cmd)

def compose_scene_video(overlay_timeline_path: Path, camera_plan_path: Path, output_video: Path, *, overlay_assets_dir: Path = DEFAULT_OVERLAY_DIR, font_file: str | None = None, width: int = DEFAULT_CANVAS_W, height: int = DEFAULT_CANVAS_H, fps: int = 30) -> None:
    ffmpeg = ensure_ffmpeg()
    overlay_timeline = load_json(overlay_timeline_path)
    camera_plan = load_json(camera_plan_path)
    scene_index = int(overlay_timeline["scene_index"])
    clean_video_path = Path(str(overlay_timeline["source_clean_video_path"]))
    if not clean_video_path.exists():
        raise RuntimeError(f"Missing clean scene video: {clean_video_path}")
    events = [item for item in overlay_timeline.get("events", []) if isinstance(item, dict)]
    duration_s = float(overlay_timeline["scene_duration_s"])
    episode_id = str(overlay_timeline["episode_id"])
    tmp_dir = TMP_DIR / episode_id / f"scene_{scene_index:02d}"
    ensure_dir(tmp_dir)
    captions_dir = ensure_dir(tmp_dir / "captions")
    overlay_assets = resolve_overlay_assets(str(overlay_assets_dir))
    comic_font = resolve_font_file(font_file)
    narration_font = resolve_narration_font_file(font_file)
    shape_font = resolve_shape_font() or comic_font
    graph: list[str] = ["[0:v]format=rgba[v0]"]
    current_label = "v0"
    stage = 1
    for event in events:
        event_id = str(event["event_id"])
        kind = str(event.get("kind", "dialogue"))
        delivery = str(event.get("delivery", "normal"))
        text = continuation_display_text(
            str(event.get("text", "")),
            int(event.get("page_index", 1)),
            int(event.get("page_count_in_group", 1)),
        )
        box_norm = list(event.get("region", {}).get("box_norm", []))
        box_x, box_y, box_w, box_h = bbox_to_pixels(box_norm, frame_w=width, frame_h=height, fallback_w=720, fallback_h=240)
        caption_file = captions_dir / f"{event_id}.txt"
        caption_file.write_text(text + "\n", encoding="utf-8")
        limits = scene_layout_bounds(kind)
        overlay_stream = f"evtbase_{stage}"
        graph.append(f"color=c=black@0.0:s={width}x{height}:d={duration_s:.4f},format=rgba[{overlay_stream}]")
        current_event_label = overlay_stream
        if kind == "narration":
            asset = overlay_assets.get("narration")
            if asset:
                overlay_w, overlay_h = fit_overlay_size_within_box_for_v2(asset, box_w, box_h, 900, 310)
                overlay_x_px = box_x + max(0, round((box_w - overlay_w) / 2))
                overlay_y_px = box_y + max(0, round((box_h - overlay_h) / 2))
                overlay_x, overlay_y, text_x, text_y, text_box_w, text_box_h = narration_layout_from_box(
                    overlay_x=overlay_x_px,
                    overlay_y=overlay_y_px,
                    overlay_w=overlay_w,
                    overlay_h=overlay_h,
                )
                svg_label = f"evt_svg_{stage}"
                next_label = f"evt_{stage}_1"
                graph.append(f"movie='{escape_filter_path(asset)}',scale={overlay_w}:{overlay_h}:flags=lanczos[{svg_label}]")
                graph.append(f"[{current_event_label}][{svg_label}]overlay={overlay_x}:{overlay_y}:format=auto[{next_label}]")
                current_event_label = next_label
            else:
                text_x = f"{box_x}+({box_w}-text_w)/2"
                text_y = f"{box_y}+({box_h}-text_h)/2"
                text_box_w = box_w
                text_box_h = box_h
            wrapped_text, font_px, line_spacing = fit_wrapped_text(
                text,
                text_box_w,
                text_box_h,
                max_font_size=int(limits["max_font"]),
                min_font_size=int(limits["min_font"]),
                max_lines=int(limits["max_lines"]),
                char_width_factor=overlay_char_width_factor(kind, delivery=delivery),
            )
            caption_file.write_text(wrapped_text + "\n", encoding="utf-8")
            next_label = f"evt_{stage}_txt"
            font_expr = f"fontfile='{escape_filter_path(narration_font or comic_font or '')}':" if (narration_font or comic_font) else ""
            graph.append(
                f"[{current_event_label}]drawtext={font_expr}textfile='{escape_filter_path(caption_file)}':"
                f"x={text_x}:y={text_y}:fontsize={font_px}:fontcolor=0x111111:"
                f"line_spacing={line_spacing}:box=0:borderw=0:shadowx=0:shadowy=0[{next_label}]"
            )
            current_event_label = next_label
        else:
            asset_key = "shout" if kind == "shout" else "dialogue"
            asset = overlay_assets.get(asset_key)
            if asset:
                overlay_w, overlay_h = fit_overlay_size_within_box_for_v2(asset, box_w, box_h, 680, 260)
                overlay_x_px = box_x + max(0, round((box_w - overlay_w) / 2))
                overlay_y_px = box_y + max(0, round((box_h - overlay_h) / 2))
                overlay_x, overlay_y, text_x, text_y, text_box_w, text_box_h = dialogue_layout_from_box(
                    overlay_x=overlay_x_px,
                    overlay_y=overlay_y_px,
                    overlay_w=overlay_w,
                    overlay_h=overlay_h,
                    shout=(kind == "shout"),
                )
                svg_label = f"evt_svg_{stage}"
                next_label = f"evt_{stage}_1"
                graph.append(f"movie='{escape_filter_path(asset)}',scale={overlay_w}:{overlay_h}:flags=lanczos[{svg_label}]")
                graph.append(f"[{current_event_label}][{svg_label}]overlay={overlay_x}:{overlay_y}:format=auto[{next_label}]")
                current_event_label = next_label
            else:
                drawbox_label = f"evt_{stage}_1"
                graph.append(
                    f"[{current_event_label}]drawbox=x={box_x}:y={box_y}:w={box_w}:h={box_h}:color=white@0.97:t=fill,"
                    f"drawbox=x={box_x}:y={box_y}:w={box_w}:h={box_h}:color=0x111111@0.92:t=4[{drawbox_label}]"
                )
                current_event_label = drawbox_label
                text_x = f"{box_x}+({box_w}-text_w)/2"
                text_y = f"{box_y}+({box_h}-text_h)/2"
                text_box_w = box_w - (2 * limits["pad_x"])
                text_box_h = box_h - (2 * limits["pad_y"])
            wrapped_text, font_px, line_spacing = fit_wrapped_text(
                text,
                max(80, text_box_w),
                max(40, text_box_h),
                max_font_size=int(limits["max_font"]),
                min_font_size=int(limits["min_font"]),
                max_lines=int(limits["max_lines"]),
                char_width_factor=overlay_char_width_factor(kind, delivery=delivery),
            )
            caption_file.write_text(wrapped_text + "\n", encoding="utf-8")
            next_label = f"evt_{stage}_txt"
            font_expr = f"fontfile='{escape_filter_path(comic_font or shape_font or '')}':" if (comic_font or shape_font) else ""
            graph.append(
                f"[{current_event_label}]drawtext={font_expr}textfile='{escape_filter_path(caption_file)}':"
                f"x={text_x}:y={text_y}:fontsize={font_px}:fontcolor=0x111111:"
                f"line_spacing={line_spacing}:box=0:borderw=0:shadowx=0:shadowy=0[{next_label}]"
            )
            current_event_label = next_label
        enable_expr = f"gte(t\\,{float(event['start_s']):.4f})*lt(t\\,{float(event['end_s']):.4f})"
        current_overlay_label = current_event_label
        next_main = f"v{stage}"
        graph.append(
            f"[{current_label}][{current_overlay_label}]overlay=0:0:format=auto:enable='{enable_expr}'[{next_main}]"
        )
        current_label = next_main
        stage += 1

    overlayed_tmp = tmp_dir / "overlayed.mp4"
    cmd_overlay = [
        ffmpeg,
        "-y",
        "-i",
        clean_video_path.as_posix(),
        "-filter_complex",
        ";".join(graph + [f"[{current_label}]format=yuv420p[vout]"]),
        "-map",
        "[vout]",
        "-map",
        "0:a",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        overlayed_tmp.as_posix(),
    ]
    ffmpeg_run(cmd_overlay)

    keyframes = [item for item in camera_plan.get("keyframes", []) if isinstance(item, dict)]
    zoom_expr = build_piecewise_linear_expr(keyframes, "zoom")
    cx_expr = build_piecewise_linear_expr(keyframes, "focus_x")
    cy_expr = build_piecewise_linear_expr(keyframes, "focus_y")
    crop_expr = (
        f"crop=w='iw/({zoom_expr})':h='ih/({zoom_expr})':"
        f"x='min(max(iw*({cx_expr})-(iw/({zoom_expr})/2),0),iw-iw/({zoom_expr}))':"
        f"y='min(max(ih*({cy_expr})-(ih/({zoom_expr})/2),0),ih-ih/({zoom_expr}))',"
        f"scale={width}:{height}:flags=lanczos,setsar=1/1,setdar=9/16,format=yuv420p"
    )
    ensure_dir(output_video.parent)
    cmd_camera = [
        ffmpeg,
        "-y",
        "-i",
        overlayed_tmp.as_posix(),
        "-vf",
        crop_expr,
        "-map",
        "0:v",
        "-map",
        "0:a",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        output_video.as_posix(),
    ]
    ffmpeg_run(cmd_camera)


def fit_overlay_size_within_box_for_v2(svg_path: Path | None, target_box_w: int, target_box_h: int, fallback_w: int, fallback_h: int) -> tuple[int, int]:
    if svg_path is None:
        return target_box_w, target_box_h
    overlay_w, overlay_h = scaled_svg_size(svg_path, target_width=min(target_box_w, fallback_w), fallback_height=fallback_h)
    scale = min(target_box_w / max(1, overlay_w), target_box_h / max(1, overlay_h))
    return max(1, round(overlay_w * scale)), max(1, round(overlay_h * scale))


def build_scene_srt_entries(overlay_timeline: dict[str, Any], scene_offset_s: float) -> list[tuple[float, float, str]]:
    entries: list[tuple[float, float, str]] = []
    for event in overlay_timeline.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        text = continuation_display_text(
            str(event.get("text", "")),
            int(event.get("page_index", 1)),
            int(event.get("page_count_in_group", 1)),
        )
        if not text:
            continue
        entries.append((scene_offset_s + float(event["start_s"]), scene_offset_s + float(event["end_s"]), text))
    return entries


def fmt_srt_time(seconds: float) -> str:
    total_ms = max(0, round(float(seconds) * 1000))
    h = total_ms // 3_600_000
    m = (total_ms % 3_600_000) // 60_000
    s = (total_ms % 60_000) // 1000
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_episode_srt(overlay_timeline_paths: list[Path], output_srt: Path) -> None:
    entries: list[tuple[float, float, str]] = []
    cursor = 0.0
    for path in overlay_timeline_paths:
        payload = load_json(path)
        entries.extend(build_scene_srt_entries(payload, cursor))
        cursor += float(payload.get("scene_duration_s", 0.0))
    lines: list[str] = []
    for idx, (start, end, text) in enumerate(entries, start=1):
        lines.extend([str(idx), f"{fmt_srt_time(start)} --> {fmt_srt_time(end)}", text, ""])
    output_srt.parent.mkdir(parents=True, exist_ok=True)
    output_srt.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def assemble_episode_video(episode_path: Path, output_video: Path | None = None, output_srt: Path | None = None) -> tuple[Path, Path]:
    ffmpeg = ensure_ffmpeg()
    episode = load_json(episode_path)
    episode_id = str(episode["episode_id"])
    render_dir = DEFAULT_RENDER_PLAN_DIR / episode_id
    composited_dir = DEFAULT_VIDEOS_COMPOSITED_DIR / episode_id
    scene_files = sorted(composited_dir.glob("scene_*.composited.mp4"))
    if not scene_files:
        raise RuntimeError(f"No composited scene videos found in {composited_dir}")
    concat_list = TMP_DIR / episode_id / "episode.concat.txt"
    ensure_dir(concat_list.parent)
    concat_list.write_text("\n".join(f"file '{path.resolve().as_posix()}'" for path in scene_files) + "\n", encoding="utf-8")
    output_video = output_video or (DEFAULT_VIDEOS_FINAL_DIR / f"{episode_id}.mp4")
    output_srt = output_srt or (DEFAULT_SUBS_FINAL_DIR / f"{episode_id}.srt")
    ensure_dir(output_video.parent)
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_list.as_posix(),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-vf",
        "setsar=1/1,setdar=9/16",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        output_video.as_posix(),
    ]
    ffmpeg_run(cmd)
    overlay_timeline_paths = sorted(render_dir.glob("scene_*.overlay_timeline.json"))
    write_episode_srt(overlay_timeline_paths, output_srt)
    return output_video, output_srt
