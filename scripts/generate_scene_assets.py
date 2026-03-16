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
MOCK_SCENE_COLORS = [
    "#0f172a",
    "#1d4ed8",
    "#b45309",
    "#065f46",
    "#7c2d12",
    "#4c1d95",
]


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
    total_paragraphs = len(paragraph_chunks)
    for paragraph_index, paragraph in enumerate(paragraph_chunks):
        chunks = [c.strip() for c in re.split(r"(?<=[.!?;:])\s+", paragraph) if c.strip()]
        if len(chunks) == 1:
            chunks = [c.strip() for c in paragraph.split(",") if c.strip()]
        if not chunks:
            chunks = [paragraph]

        merged: list[str] = []
        for chunk in chunks:
            if not merged:
                merged.append(chunk)
                continue
            if len(chunk) < 32 and len(merged[-1]) < 90:
                merged[-1] = f"{merged[-1]} {chunk}"
            else:
                merged.append(chunk)

        paragraph_blocks = [trim_caption(piece) for piece in merged if trim_caption(piece)]
        total = len(paragraph_blocks)
        is_middle_paragraph = total_paragraphs >= 3 and 0 < paragraph_index < total_paragraphs - 1
        for idx, piece in enumerate(paragraph_blocks):
            text = piece
            if total >= 3 and 0 < idx < total - 1:
                text = trim_caption(with_mid_ellipsis(piece))
            elif is_middle_paragraph and total == 1:
                text = trim_caption(with_mid_ellipsis(piece))
            all_blocks.append(
                {
                    "text": text,
                    "paragraph_index": paragraph_index,
                    "paragraph_block_index": idx,
                    "paragraph_blocks_total": total,
                }
            )

    final_blocks = all_blocks[:max_blocks]
    if len(final_blocks) >= 3:
        for idx in range(1, len(final_blocks) - 1):
            text = str(final_blocks[idx].get("text", ""))
            if not text.startswith("...") or not text.endswith("..."):
                final_blocks[idx]["text"] = trim_caption(with_mid_ellipsis(text))
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
        out.append({"speaker": speaker, "line": trim_caption(line, max_len=170)})
    return out


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


def save_tts_audio(client, model: str, voice: str, narration: str, output_audio: Path) -> None:
    speech = client.audio.speech

    # Preferred SDK path: avoids deprecation warning from non-streaming stream_to_file.
    with_streaming = getattr(speech, "with_streaming_response", None)
    if with_streaming is not None and hasattr(with_streaming, "create"):
        with with_streaming.create(
            model=model,
            voice=voice,
            input=narration,
            response_format="mp3",
        ) as tts_stream:
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
    tts_response = speech.create(
        model=model,
        voice=voice,
        input=narration,
        response_format="mp3",
    )
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


def ensure_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    raise RuntimeError("ffmpeg not found. Install ffmpeg first.")


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
    parser.add_argument("--image-model", default=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1"), help="OpenAI image model")
    parser.add_argument("--image-size", default=os.getenv("OPENAI_IMAGE_SIZE", "1024x1024"), help="Image size, e.g. 1024x1024")
    parser.add_argument("--tts-model", default=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"), help="OpenAI TTS model")
    parser.add_argument("--tts-voice", default=os.getenv("OPENAI_TTS_VOICE", "alloy"), help="OpenAI TTS voice id")
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

        assets_root = Path(args.assets_dir) / str(episode["episode_id"])
        scenes_dir = assets_root / "scenes"
        scenes_dir.mkdir(parents=True, exist_ok=True)

        load_dotenv_if_present(Path(args.dotenv))
        ffmpeg = ensure_ffmpeg()

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
            duration = int(scene["estimated_seconds"])
            narration = str(scene["narration"]).strip()
            visual_prompt = str(scene["visual_prompt"]).strip()
            scene_dialogue = normalize_scene_dialogue(scene.get("dialogue"))
            prev_narration = ""
            next_narration = ""
            if scene_pos > 0:
                prev_narration = str(scenes[scene_pos - 1].get("narration", "")).strip()
            if scene_pos + 1 < len(scenes):
                next_narration = str(scenes[scene_pos + 1].get("narration", "")).strip()

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
            block_durations = split_duration_slots(duration, len(caption_blocks))

            audio_path = scenes_dir / f"scene_{idx:02d}.mp3"
            base_prompt_path = scenes_dir / f"scene_{idx:02d}.prompt.txt"
            base_prompt = build_image_prompt(episode, scene)
            base_prompt_path.write_text(base_prompt + "\n", encoding="utf-8")

            block_assets: list[dict] = []
            dialogue_slots = 0
            dialogue_start = 10**9
            if scene_dialogue:
                dialogue_slots = min(len(scene_dialogue), max(1, len(caption_blocks) - 1))
                dialogue_start = len(caption_blocks) - dialogue_slots + 1
            for block_idx, block_payload in enumerate(caption_blocks, start=1):
                block_text = str(block_payload.get("text", "")).strip()
                block_dialogue = {}
                if scene_dialogue and block_idx >= dialogue_start:
                    d_idx = block_idx - dialogue_start
                    if d_idx < len(scene_dialogue):
                        block_dialogue = scene_dialogue[d_idx]
                block_speaker = str(block_dialogue.get("speaker", "")).strip()
                block_line = str(block_dialogue.get("line", "")).strip()
                block_img = scenes_dir / f"scene_{idx:02d}_block_{block_idx:02d}.png"
                block_prompt_path = scenes_dir / f"scene_{idx:02d}_block_{block_idx:02d}.prompt.txt"
                block_prompt = build_block_image_prompt(
                    base_prompt=base_prompt,
                    block_text=block_text,
                    dialogue_speaker=block_speaker,
                    dialogue_line=block_line,
                    block_index=block_idx,
                    total_blocks=len(caption_blocks),
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
                        "duration_seconds": int(block_durations[block_idx - 1]),
                        "image_path": str(block_img),
                        "dialogue_speaker": block_speaker,
                        "dialogue_line": block_line,
                        "prompt_path": str(block_prompt_path),
                        "prompt_text": block_prompt,
                    }
                )

            if args.mock:
                create_mock_audio(ffmpeg, audio_path, duration)
                for block in block_assets:
                    color = MOCK_SCENE_COLORS[(idx + int(block["block_index"])) % len(MOCK_SCENE_COLORS)]
                    create_mock_image(ffmpeg, Path(block["image_path"]), color=color)
            else:
                try:
                    save_tts_audio(
                        client=client,
                        model=args.tts_model,
                        voice=args.tts_voice,
                        narration=narration,
                        output_audio=audio_path,
                    )
                    for block in block_assets:
                        image_resp = client.images.generate(
                            model=args.image_model,
                            prompt=str(block["prompt_text"]),
                            size=args.image_size,
                        )
                        Path(str(block["image_path"])).write_bytes(first_image_bytes(image_resp))
                except Exception as exc:  # SDK raises typed exceptions; keep generic for compatibility.
                    if args.fallback_mock_on_billing_error and is_billing_limit_error(exc):
                        print(
                            "WARNING: OpenAI billing limit reached. "
                            f"Using mock assets for scene {idx:02d} of {episode['episode_id']}."
                        )
                        create_mock_audio(ffmpeg, audio_path, duration)
                        for block in block_assets:
                            color = MOCK_SCENE_COLORS[(idx + int(block["block_index"])) % len(MOCK_SCENE_COLORS)]
                            create_mock_image(ffmpeg, Path(block["image_path"]), color=color)
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
                            "dialogue_speaker": str(block.get("dialogue_speaker", "")),
                            "dialogue_line": str(block.get("dialogue_line", "")),
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
                "image_model": args.image_model if not args.mock else None,
                "image_size": args.image_size if not args.mock else None,
                "tts_model": args.tts_model if not args.mock else None,
                "tts_voice": args.tts_voice if not args.mock else None,
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
