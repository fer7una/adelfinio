#!/usr/bin/env python3
"""Generate per-scene images and voice audio from an episode JSON.

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


def default_narrator_profile() -> dict:
    try:
        narrator_wps = float(os.getenv("VIDEO_NARRATOR_WPS", "2.55"))
    except ValueError:
        narrator_wps = 2.55
    return {
        "tts_voice": os.getenv("VIDEO_NARRATOR_TTS_VOICE", os.getenv("OPENAI_TTS_VOICE", "alloy")),
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


def split_caption_blocks(narration: str, max_blocks: int = 3) -> list[dict]:
    raw = (narration or "").strip()
    if not raw:
        return []

    paragraph_chunks = [normalize_ws(p) for p in re.split(r"\n\s*\n+", raw) if normalize_ws(p)]
    if not paragraph_chunks:
        paragraph_chunks = [normalize_ws(raw)]

    all_blocks: list[dict] = []
    for paragraph_index, paragraph in enumerate(paragraph_chunks):
        chunks = [c.strip() for c in re.split(r"(?<=[.!?;:])\s+", paragraph) if c.strip()]
        if not chunks:
            chunks = [paragraph]
        paragraph_blocks = [normalize_ws(piece) for piece in chunks if normalize_ws(piece)]
        total = len(paragraph_blocks)
        for idx, piece in enumerate(paragraph_blocks):
            all_blocks.append(
                {
                    "text": piece,
                    "paragraph_index": paragraph_index,
                    "paragraph_block_index": idx,
                    "paragraph_blocks_total": total,
                }
            )

    final_blocks = all_blocks[:max_blocks]
    return final_blocks


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
        line = normalize_ws(str(item.get("line", "")))
        if not speaker or not line:
            continue
        out.append(
            {
                "speaker": speaker,
                "line": trim_caption(line, max_len=170),
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
        voice_profile = payload.get("voice_profile")
        if not display_name or not isinstance(voice_profile, dict):
            continue
        profiles[display_name] = voice_profile
        profiles[normalize_ws(character_id).lower()] = voice_profile
    return profiles


def fallback_voice_profile(speaker: str) -> dict:
    low = normalize_ws(speaker).lower()
    narrator_profile = default_narrator_profile()
    profile = dict(narrator_profile)
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
        return existing
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
    narration: str,
    scene_dialogue: list[dict],
    character_profiles: dict[str, dict],
) -> list[dict]:
    if scene_dialogue:
        narrator_profile = default_narrator_profile()
        blocks: list[dict] = [
            {
                "text": normalize_ws(narration),
                "paragraph_index": 0,
                "paragraph_block_index": 0,
                "paragraph_blocks_total": len(scene_dialogue) + 1,
                "dialogue_speaker": "",
                "dialogue_line": "",
                "dialogue_delivery": "normal",
                "audio_text": normalize_ws(narration),
                "voice_profile": dict(narrator_profile),
                "duration_seconds": estimate_block_seconds(normalize_ws(narration), narrator_profile, "normal"),
            }
        ]
        total = len(scene_dialogue) + 1
        for idx, row in enumerate(scene_dialogue, start=1):
            speaker = str(row.get("speaker", "")).strip()
            line = str(row.get("line", "")).strip()
            delivery = str(row.get("delivery", "normal")).strip() or "normal"
            voice_profile = resolve_voice_profile(speaker, character_profiles)
            blocks.append(
                {
                    "text": line or normalize_ws(narration),
                    "paragraph_index": 0,
                    "paragraph_block_index": idx,
                    "paragraph_blocks_total": total,
                    "dialogue_speaker": speaker,
                    "dialogue_line": line,
                    "dialogue_delivery": delivery,
                    "audio_text": line,
                    "voice_profile": voice_profile,
                    "duration_seconds": estimate_block_seconds(line, voice_profile, delivery),
                }
            )
        return blocks

    caption_blocks = split_caption_blocks(narration, max_blocks=3)
    if not caption_blocks:
        caption_blocks = [
            {
                "text": trim_caption(narration),
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
        blocks.append(
            {
                "text": text,
                "paragraph_index": int(item.get("paragraph_index", 0)),
                "paragraph_block_index": int(item.get("paragraph_block_index", 0)),
                "paragraph_blocks_total": int(item.get("paragraph_blocks_total", len(caption_blocks))),
                "dialogue_speaker": "",
                "dialogue_line": "",
                "dialogue_delivery": "normal",
                "audio_text": text,
                "voice_profile": voice_profile,
                "duration_seconds": estimate_block_seconds(text, voice_profile, "normal"),
            }
        )
    return blocks


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
        "Composicion vertical para video 9:16, profundidad dramatica, encuadre de vineta."
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
) -> str:
    dialogue_ref = f"{dialogue_speaker}: {dialogue_line}" if dialogue_speaker and dialogue_line else "sin dialogo adicional"
    speaker_visual = (
        f"Mostrar claramente a {dialogue_speaker} pronunciando su frase, con expresion facial y gestual coherente."
        if dialogue_speaker and dialogue_line
        else "No hay parlamento directo en este bloque."
    )
    return (
        f"{base_prompt}\n"
        f"MOMENTO {block_index}/{total_blocks}: {block_text}\n"
        f"REACCION PERSONAJE: {dialogue_ref}\n"
        f"FOCO VISUAL DIALOGO: {speaker_visual}\n"
        "INDICACIONES EXTRA: accion intensa, composicion compacta, expresiones faciales fuertes, "
        "color saturado, energia de comic historico, sin aspecto de postal.\n"
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
        image_model = args.image_model or os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
        image_size = args.image_size or os.getenv("OPENAI_IMAGE_SIZE", "1024x1024")
        image_quality = normalize_image_quality(args.image_quality or os.getenv("OPENAI_IMAGE_QUALITY"))
        tts_model = args.tts_model or os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        tts_voice = args.tts_voice or os.getenv("OPENAI_TTS_VOICE", "alloy")
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
            base_prompt_path = scenes_dir / f"scene_{idx:02d}.prompt.txt"
            base_prompt = build_image_prompt(episode, scene)
            base_prompt_path.write_text(base_prompt + "\n", encoding="utf-8")

            scene_blocks = build_scene_block_plan(narration, scene_dialogue, character_profiles)
            block_assets: list[dict] = []
            for block_idx, block_payload in enumerate(scene_blocks, start=1):
                block_text = str(block_payload.get("text", "")).strip()
                block_speaker = str(block_payload.get("dialogue_speaker", "")).strip()
                block_line = str(block_payload.get("dialogue_line", "")).strip()
                block_delivery = str(block_payload.get("dialogue_delivery", "normal")).strip() or "normal"
                block_img = scenes_dir / f"scene_{idx:02d}_block_{block_idx:02d}.png"
                block_audio = scenes_dir / f"scene_{idx:02d}_block_{block_idx:02d}.mp3"
                block_prompt_path = scenes_dir / f"scene_{idx:02d}_block_{block_idx:02d}.prompt.txt"
                block_prompt = build_block_image_prompt(
                    base_prompt=base_prompt,
                    block_text=block_text,
                    dialogue_speaker=block_speaker,
                    dialogue_line=block_line,
                    block_index=block_idx,
                    total_blocks=len(scene_blocks),
                    prev_scene_narration=prev_narration,
                    next_scene_narration=next_narration,
                )
                block_prompt_path.write_text(block_prompt + "\n", encoding="utf-8")
                block_assets.append(
                    {
                        "block_index": block_idx,
                        "text": block_text,
                        "paragraph_index": int(block_payload.get("paragraph_index", 0)),
                        "paragraph_block_index": int(block_payload.get("paragraph_block_index", 0)),
                        "paragraph_blocks_total": int(block_payload.get("paragraph_blocks_total", 1)),
                        "duration_seconds": int(block_payload.get("duration_seconds", 1)),
                        "image_path": str(block_img),
                        "audio_path": str(block_audio),
                        "audio_text": str(block_payload.get("audio_text", block_line or block_text)),
                        "dialogue_speaker": block_speaker,
                        "dialogue_line": block_line,
                        "dialogue_delivery": block_delivery,
                        "voice_profile": dict(block_payload.get("voice_profile", narrator_profile)),
                        "prompt_path": str(block_prompt_path),
                        "prompt_text": block_prompt,
                    }
                )
            duration = sum(int(block["duration_seconds"]) for block in block_assets)
            if duration <= 0:
                duration = max(1, int(scene.get("estimated_seconds", 1)))

            if args.mock:
                for block in block_assets:
                    create_mock_audio(ffmpeg, Path(str(block["audio_path"])), int(block["duration_seconds"]))
                    color = MOCK_SCENE_COLORS[(idx + int(block["block_index"])) % len(MOCK_SCENE_COLORS)]
                    create_mock_image(ffmpeg, Path(block["image_path"]), color=color)
                concat_audio_segments(
                    ffmpeg,
                    [Path(str(block["audio_path"])) for block in block_assets],
                    audio_path,
                    scenes_dir,
                )
            else:
                try:
                    for block in block_assets:
                        profile = block.get("voice_profile") if isinstance(block.get("voice_profile"), dict) else narrator_profile
                        target_seconds = int(block["duration_seconds"])
                        raw_audio = Path(str(block["audio_path"])).with_suffix(".raw.mp3")
                        profile_tone = str(profile.get("tone", narrator_profile["tone"])).replace("_", " ")
                        delivery = str(block.get("dialogue_delivery", "normal"))
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
                            voice=str(profile.get("tts_voice", tts_voice or narrator_profile["tts_voice"])),
                            narration=str(block["audio_text"]),
                            output_audio=raw_audio,
                            instructions=instructions,
                            speed=speed_hint,
                        )
                        fit_audio_to_duration(ffmpeg, raw_audio, Path(str(block["audio_path"])), target_seconds)
                        image_resp = client.images.generate(
                            model=image_model,
                            prompt=str(block["prompt_text"]),
                            size=image_size,
                            quality=image_quality,
                        )
                        Path(str(block["image_path"])).write_bytes(first_image_bytes(image_resp))
                    concat_audio_segments(
                        ffmpeg,
                        [Path(str(block["audio_path"])) for block in block_assets],
                        audio_path,
                        scenes_dir,
                    )
                except Exception as exc:  # SDK raises typed exceptions; keep generic for compatibility.
                    if args.fallback_mock_on_billing_error and is_billing_limit_error(exc):
                        print(
                            "WARNING: OpenAI billing limit reached. "
                            f"Using mock assets for scene {idx:02d} of {episode['episode_id']}."
                        )
                        for block in block_assets:
                            create_mock_audio(ffmpeg, Path(str(block["audio_path"])), int(block["duration_seconds"]))
                            color = MOCK_SCENE_COLORS[(idx + int(block["block_index"])) % len(MOCK_SCENE_COLORS)]
                            create_mock_image(ffmpeg, Path(block["image_path"]), color=color)
                        concat_audio_segments(
                            ffmpeg,
                            [Path(str(block["audio_path"])) for block in block_assets],
                            audio_path,
                            scenes_dir,
                        )
                        used_billing_fallback = True
                    else:
                        raise RuntimeError(openai_error_message(exc)) from exc

            image_paths = [str(block["image_path"]) for block in block_assets]
            prompt_paths = [str(block["prompt_path"]) for block in block_assets]

            manifest_scenes.append(
                {
                    "scene_index": idx,
                    "estimated_seconds": duration,
                    "narration": narration,
                    "visual_prompt": visual_prompt,
                    "dialogue": scene_dialogue,
                    "caption_blocks": [
                        {
                            "block_index": int(block["block_index"]),
                            "text": str(block["text"]),
                            "paragraph_index": int(block.get("paragraph_index", 0)),
                            "paragraph_block_index": int(block.get("paragraph_block_index", 0)),
                            "paragraph_blocks_total": int(block.get("paragraph_blocks_total", 1)),
                            "duration_seconds": int(block["duration_seconds"]),
                            "image_path": str(block["image_path"]),
                            "audio_path": str(block["audio_path"]),
                            "dialogue_speaker": str(block.get("dialogue_speaker", "")),
                            "dialogue_line": str(block.get("dialogue_line", "")),
                            "dialogue_delivery": str(block.get("dialogue_delivery", "normal")),
                            "prompt_path": str(block["prompt_path"]),
                        }
                        for block in block_assets
                    ],
                    "image_path": image_paths[0],
                    "image_paths": image_paths,
                    "audio_path": str(audio_path),
                    "prompt_path": str(base_prompt_path),
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
