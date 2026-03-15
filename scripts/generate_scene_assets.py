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
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ASSETS_DIR = ROOT / "artifacts" / "scene_assets"


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


def build_image_prompt(episode: dict, scene: dict) -> str:
    episode_title = str(episode.get("title", "")).strip()
    scene_prompt = str(scene.get("visual_prompt", "")).strip()
    scene_narration = str(scene.get("narration", "")).strip()
    characters = episode.get("characters") or []
    cast = ", ".join(str(x) for x in characters[:5]) if characters else "sin personajes nombrados"

    style_guide = (
        "Ilustracion historica cinematografica, alta fidelidad, tono documental, "
        "sin texto incrustado, sin logos, sin watermark, sin UI. "
        "Vestuario altomedieval iberico coherente. "
        "Composicion vertical para video 9:16."
    )

    return (
        f"TITULO EPISODIO: {episode_title}\n"
        f"ESCENA {scene.get('scene_index')}: {scene_prompt}\n"
        f"CONTEXTO NARRATIVO: {scene_narration}\n"
        f"ELENCO REFERENCIA: {cast}\n"
        f"ESTILO OBLIGATORIO: {style_guide}"
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


def create_mock_image(ffmpeg: str, output_png: Path, width: int = 1080, height: int = 1920) -> None:
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=#1f2937:s={width}x{height}",
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
        for scene in scenes:
            idx = int(scene["scene_index"])
            duration = int(scene["estimated_seconds"])
            narration = str(scene["narration"]).strip()
            visual_prompt = str(scene["visual_prompt"]).strip()

            img_path = scenes_dir / f"scene_{idx:02d}.png"
            audio_path = scenes_dir / f"scene_{idx:02d}.mp3"
            prompt_path = scenes_dir / f"scene_{idx:02d}.prompt.txt"
            prompt_text = build_image_prompt(episode, scene)
            prompt_path.write_text(prompt_text + "\n", encoding="utf-8")

            if args.mock:
                create_mock_image(ffmpeg, img_path)
                create_mock_audio(ffmpeg, audio_path, duration)
            else:
                try:
                    image_resp = client.images.generate(
                        model=args.image_model,
                        prompt=prompt_text,
                        size=args.image_size,
                    )
                    img_path.write_bytes(first_image_bytes(image_resp))

                    save_tts_audio(
                        client=client,
                        model=args.tts_model,
                        voice=args.tts_voice,
                        narration=narration,
                        output_audio=audio_path,
                    )
                except Exception as exc:  # SDK raises typed exceptions; keep generic for compatibility.
                    if args.fallback_mock_on_billing_error and is_billing_limit_error(exc):
                        print(
                            "WARNING: OpenAI billing limit reached. "
                            f"Using mock assets for scene {idx:02d} of {episode['episode_id']}."
                        )
                        create_mock_image(ffmpeg, img_path)
                        create_mock_audio(ffmpeg, audio_path, duration)
                        used_billing_fallback = True
                    else:
                        raise RuntimeError(openai_error_message(exc)) from exc

            manifest_scenes.append(
                {
                    "scene_index": idx,
                    "estimated_seconds": duration,
                    "narration": narration,
                    "visual_prompt": visual_prompt,
                    "image_path": str(img_path),
                    "audio_path": str(audio_path),
                    "prompt_path": str(prompt_path),
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
