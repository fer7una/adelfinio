#!/usr/bin/env python3
"""Compose a final vertical MP4 from scene assets generated per episode."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ASSETS_DIR = ROOT / "artifacts" / "scene_assets"
DEFAULT_VIDEO_DIR = ROOT / "artifacts" / "videos" / "final"
DEFAULT_SUBS_DIR = ROOT / "artifacts" / "subtitles" / "final"
TMP_DIR = ROOT / ".tmp" / "compose_final"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid JSON payload: {path}")
    return payload


def fmt_srt_time(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d},000"


def write_srt(episode: dict, output_srt: Path) -> None:
    output_srt.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    cursor = 0
    for idx, scene in enumerate(episode["scenes"], start=1):
        duration = int(scene["estimated_seconds"])
        start = fmt_srt_time(cursor)
        end = fmt_srt_time(cursor + duration)
        text = str(scene["narration"]).strip().replace("\n", " ")
        lines.extend([str(idx), f"{start} --> {end}", text, ""])
        cursor += duration
    output_srt.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def build_scene_segment(
    ffmpeg: str,
    image_path: Path,
    audio_path: Path,
    output_segment: Path,
    width: int,
    height: int,
    fps: int,
    duration_s: int,
) -> None:
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},format=yuv420p"
    )
    af = f"apad=pad_dur={duration_s},atrim=0:{duration_s}"

    cmd = [
        ffmpeg,
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-t",
        str(duration_s),
        "-i",
        image_path.as_posix(),
        "-i",
        audio_path.as_posix(),
        "-vf",
        vf,
        "-af",
        af,
        "-shortest",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        output_segment.as_posix(),
    ]
    subprocess.run(cmd, check=True)


def compose_concat(ffmpeg: str, concat_list: Path, output_no_subs: Path) -> None:
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
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        output_no_subs.as_posix(),
    ]
    subprocess.run(cmd, check=True)


def burn_subtitles(ffmpeg: str, input_video: Path, output_video: Path, srt_path: Path) -> None:
    vf = f"subtitles={srt_path.as_posix()}"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        input_video.as_posix(),
        "-vf",
        vf,
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
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", required=True, help="Path to episode JSON")
    parser.add_argument("--assets-dir", default=str(DEFAULT_ASSETS_DIR), help="Scene assets root directory")
    parser.add_argument("--output-video", default=None, help="Final output MP4 path")
    parser.add_argument("--output-srt", default=None, help="Output subtitle path")
    parser.add_argument("--fps", type=int, default=30, help="Video fps")
    parser.add_argument("--width", type=int, default=1080, help="Output width")
    parser.add_argument("--height", type=int, default=1920, help="Output height")
    parser.add_argument("--no-burn-subtitles", action="store_true", help="Do not burn subtitles into final MP4")
    args = parser.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ERROR: ffmpeg not found. Install ffmpeg first.")
        return 1

    try:
        episode_path = Path(args.episode)
        episode = load_json(episode_path)
        episode_id = str(episode["episode_id"])
        scenes = episode.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise RuntimeError("Episode has no scenes.")

        assets_root = Path(args.assets_dir) / episode_id
        manifest_path = assets_root / "manifest.json"
        if not manifest_path.exists():
            raise RuntimeError(
                f"Missing scene asset manifest: {manifest_path}. "
                "Run scripts/generate_scene_assets.py first."
            )
        manifest = load_json(manifest_path)
        manifest_scenes = manifest.get("scenes")
        if not isinstance(manifest_scenes, list) or len(manifest_scenes) != len(scenes):
            raise RuntimeError("Scene asset manifest does not match episode scenes.")

        output_video = (
            Path(args.output_video)
            if args.output_video
            else DEFAULT_VIDEO_DIR / f"{episode_id}.mp4"
        )
        output_srt = (
            Path(args.output_srt)
            if args.output_srt
            else DEFAULT_SUBS_DIR / f"{episode_id}.srt"
        )
        output_video.parent.mkdir(parents=True, exist_ok=True)
        output_srt.parent.mkdir(parents=True, exist_ok=True)

        tmp_episode_dir = TMP_DIR / episode_id
        tmp_episode_dir.mkdir(parents=True, exist_ok=True)
        concat_list = tmp_episode_dir / "concat.txt"
        merged_video = tmp_episode_dir / "merged_no_subs.mp4"

        concat_lines: list[str] = []
        for scene in manifest_scenes:
            idx = int(scene["scene_index"])
            duration = int(scene["estimated_seconds"])
            image_path = Path(str(scene["image_path"]))
            audio_path = Path(str(scene["audio_path"]))
            if not image_path.exists():
                raise RuntimeError(f"Missing image asset: {image_path}")
            if not audio_path.exists():
                raise RuntimeError(f"Missing audio asset: {audio_path}")

            segment_path = tmp_episode_dir / f"segment_{idx:02d}.mp4"
            build_scene_segment(
                ffmpeg=ffmpeg,
                image_path=image_path,
                audio_path=audio_path,
                output_segment=segment_path,
                width=args.width,
                height=args.height,
                fps=args.fps,
                duration_s=duration,
            )
            concat_lines.append(f"file '{segment_path.as_posix()}'")

        concat_list.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
        compose_concat(ffmpeg, concat_list, merged_video)
        write_srt(episode, output_srt)

        if args.no_burn_subtitles:
            shutil.copy2(merged_video, output_video)
        else:
            burn_subtitles(ffmpeg, merged_video, output_video, output_srt)

        print(f"Composed final video: {output_video}")
        print(f"Subtitles: {output_srt}")
        print(f"Assets manifest: {manifest_path}")
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: ffmpeg command failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
