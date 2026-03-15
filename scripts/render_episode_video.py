#!/usr/bin/env python3
"""Render a local vertical MP4 from an episode JSON using ffmpeg."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "videos"
DEFAULT_SUBS_DIR = ROOT / "artifacts" / "subtitles"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def fmt_srt_time(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d},000"


def write_srt(episode: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    cursor = 0
    for idx, scene in enumerate(episode["scenes"], start=1):
        duration = int(scene["estimated_seconds"])
        start = fmt_srt_time(cursor)
        end = fmt_srt_time(cursor + duration)
        text = scene["narration"].strip().replace("\n", " ")
        lines.extend([str(idx), f"{start} --> {end}", text, ""])
        cursor += duration
    with output_path.open("w", encoding="utf-8") as handle:
        handle.write("\n".join(lines).strip() + "\n")


def wrap_text(raw: str, width: int = 42) -> str:
    words = raw.split()
    if not words:
        return ""
    out: list[str] = []
    line: list[str] = []
    current = 0
    for word in words:
        extra = len(word) + (1 if line else 0)
        if current + extra > width:
            out.append(" ".join(line))
            line = [word]
            current = len(word)
        else:
            line.append(word)
            current += extra
    if line:
        out.append(" ".join(line))
    return "\\n".join(out)


def write_scene_textfiles(episode: dict, temp_dir: Path) -> list[Path]:
    temp_dir.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for scene in episode["scenes"]:
        path = temp_dir / f"scene_{scene['scene_index']:02d}.txt"
        content = wrap_text(scene["narration"], width=42)
        with path.open("w", encoding="utf-8") as handle:
            handle.write(content + "\n")
        files.append(path)
    return files


def write_static_text(path: Path, content: str, width: int) -> Path:
    wrapped = wrap_text(content, width=width)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(wrapped + "\n")
    return path


def build_filter(episode: dict, scene_text_files: list[Path], title_file: Path, footer_file: Path) -> str:
    layers: list[str] = [
        "drawbox=x=0:y=0:w=iw:h=210:color=black@0.45:t=fill",
        "drawtext="
        f"textfile='{title_file.as_posix()}':"
        "x=(w-text_w)/2:y=56:"
        "fontsize=50:fontcolor=white",
    ]

    cursor = 0
    for idx, scene in enumerate(episode["scenes"]):
        start = cursor
        end = cursor + int(scene["estimated_seconds"])
        text_file = scene_text_files[idx].as_posix()
        layers.append(
            "drawtext="
            f"textfile='{text_file}':"
            "x=70:y=(h-text_h)/2:"
            "fontsize=44:fontcolor=white:line_spacing=12:"
            "box=1:boxcolor=black@0.50:boxborderw=18:"
            f"enable='between(t,{start},{end})'"
        )
        cursor = end

    layers.append(
        "drawtext="
        f"textfile='{footer_file.as_posix()}':"
        "x=(w-text_w)/2:y=h-88:"
        "fontsize=34:fontcolor=white@0.9"
    )

    return ",".join(layers)


def render(episode: dict, episode_path: Path, output_video: Path, output_srt: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found. Install ffmpeg to render local videos.")

    duration = int(episode.get("duration_seconds") or sum(int(s["estimated_seconds"]) for s in episode["scenes"]))

    temp_dir = ROOT / ".tmp" / "render" / episode["episode_id"]
    scene_text_files = write_scene_textfiles(episode, temp_dir)
    title_file = write_static_text(temp_dir / "title.txt", episode["title"], width=34)
    footer_file = write_static_text(temp_dir / "footer.txt", f"@adelfinio  |  {episode['episode_type']}", width=42)
    write_srt(episode, output_srt)

    vf = build_filter(episode, scene_text_files, title_file, footer_file)

    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=#1e2633:s=1080x1920:d={duration}",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-shortest",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "30",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        output_video.as_posix(),
    ]

    output_video.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", required=True, help="Path to episode JSON")
    parser.add_argument("--output-video", default=None, help="Output mp4 path")
    parser.add_argument("--output-srt", default=None, help="Output subtitle path")
    args = parser.parse_args()

    episode_path = Path(args.episode)
    episode = load_json(episode_path)

    output_video = (
        Path(args.output_video)
        if args.output_video
        else DEFAULT_OUTPUT_DIR / f"{episode['episode_id']}.mp4"
    )
    output_srt = (
        Path(args.output_srt)
        if args.output_srt
        else DEFAULT_SUBS_DIR / f"{episode['episode_id']}.srt"
    )

    render(episode, episode_path, output_video, output_srt)
    print(f"Rendered video: {output_video}")
    print(f"Rendered subtitles: {output_srt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
