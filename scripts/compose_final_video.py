#!/usr/bin/env python3
"""Compose a final vertical MP4 from scene assets generated per episode."""

from __future__ import annotations

import argparse
import json
import os
import re
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


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_terminal_punctuation(text: str) -> str:
    clean = normalize_ws(text)
    clean = clean.replace(":", ".")
    clean = re.sub(r"\s*\.\s*\.\s*\.", "...", clean)
    return clean


def trim_caption(text: str, max_len: int = 140) -> str:
    clean = normalize_terminal_punctuation(text)
    if len(clean) <= max_len:
        return clean
    return normalize_terminal_punctuation(clean[: max_len - 3].rstrip(" ,;:") + "...")


def with_mid_ellipsis(text: str) -> str:
    base = normalize_ws(text).strip(". ")
    if not base:
        return "..."
    return f"... {base} ..."


def wrap_text(text: str, width: int = 22, max_lines: int = 3) -> str:
    words = normalize_ws(text).split()
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
    if len(out) > max_lines:
        out = out[:max_lines]
        if not out[-1].endswith("..."):
            out[-1] = out[-1].rstrip(" ,;:") + "..."
    return "\n".join(out)


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
    count = max(1, int(parts))
    if count == 1:
        return [total]
    base = total // count
    slots = [base for _ in range(count)]
    remainder = total - (base * count)
    for idx in range(remainder):
        slots[idx] += 1
    for idx, value in enumerate(slots):
        if value <= 0:
            slots[idx] = 1
    adjust = total - sum(slots)
    if adjust != 0:
        slots[-1] += adjust
    return slots


def resolve_font_file(explicit_font_file: str | None) -> str | None:
    if explicit_font_file:
        candidate = Path(explicit_font_file)
        if candidate.exists():
            return candidate.as_posix()
        raise RuntimeError(f"Font file not found: {candidate}")
    candidates = [
        "/usr/share/fonts/truetype/medievalsharp/MedievalSharp-Regular.ttf",
        "/usr/share/fonts/truetype/cinzel/Cinzel-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    ]
    for item in candidates:
        if Path(item).exists():
            return item
    return None


def resolve_shape_font() -> str | None:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for item in candidates:
        if Path(item).exists():
            return item
    return None


def block_position(scene_index: int, paragraph_index: int) -> tuple[str, str]:
    positions = [
        ("72", "h-text_h-210"),
        ("w-text_w-72", "h-text_h-170"),
    ]
    return positions[(scene_index + paragraph_index) % len(positions)]


def dialogue_bubble_position(scene_index: int, block_index: int) -> tuple[str, str]:
    positions = [
        ("90", "120"),
        ("w-750", "140"),
        ("110", "230"),
        ("w-770", "250"),
    ]
    return positions[(scene_index + block_index) % len(positions)]


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


def normalize_scene_dialogue(dialogue_payload) -> list[dict]:
    if not isinstance(dialogue_payload, list):
        return []
    out: list[dict] = []
    for item in dialogue_payload:
        if not isinstance(item, dict):
            continue
        speaker = normalize_ws(str(item.get("speaker", "")))
        line = normalize_terminal_punctuation(str(item.get("line", "")))
        if not speaker or not line:
            continue
        out.append({"speaker": speaker, "line": trim_caption(line, max_len=170)})
    return out


def build_block_filter(
    scene_index: int,
    block_index: int,
    paragraph_index: int,
    block_text: str,
    dialogue_speaker: str,
    dialogue_line: str,
    tmp_episode_dir: Path,
    width: int,
    height: int,
    fps: int,
    duration_s: int,
    font_file: str | None,
) -> str:
    frames = max(1, duration_s * fps)
    zoom_expr = "if(lte(on,1),1.03,min(1.03+on*0.0018,1.28))"
    drift_x = [0.28, -0.22, 0.18, -0.16][(scene_index + block_index) % 4]
    drift_y = [-0.10, 0.12, -0.08, 0.14][(scene_index + block_index) % 4]
    x_expr = f"min(max(iw/2-(iw/zoom/2)+on*{drift_x:.2f},0),iw-iw/zoom)"
    y_expr = f"min(max(ih/2-(ih/zoom/2)+on*{drift_y:.2f},0),ih-ih/zoom)"

    filters: list[str] = [
        f"scale={width}:{height}:force_original_aspect_ratio=increase",
        f"crop={width}:{height}",
        (
            f"zoompan=z='{zoom_expr}':"
            f"x='{x_expr}':"
            f"y='{y_expr}':"
            f"d={frames}:s={width}x{height}:fps={fps}"
        ),
        "eq=contrast=1.10:saturation=1.24:brightness=-0.01",
        "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.10:t=22",
        "format=yuv420p",
    ]

    caption_dir = tmp_episode_dir / "captions"
    caption_dir.mkdir(parents=True, exist_ok=True)
    caption_file = caption_dir / f"scene_{scene_index:02d}_block_{block_index:02d}.txt"
    clean_block_text = trim_caption(block_text, max_len=150)
    caption_file.write_text(wrap_text(clean_block_text, width=24, max_lines=3) + "\n", encoding="utf-8")
    text_x, text_y = block_position(scene_index, paragraph_index)
    font_expr = f"fontfile='{font_file}':" if font_file else ""
    dialogue_mode = bool(dialogue_line)
    if not dialogue_mode:
        filters.append(
            "drawtext="
            f"{font_expr}"
            f"textfile='{caption_file.as_posix()}':"
            f"x={text_x}:y={text_y}:"
            "fontsize=42:fontcolor=0x111111:line_spacing=6:"
            "box=1:boxcolor=white@0.97:boxborderw=16:"
            "borderw=1:bordercolor=0x111111@0.92:"
            "shadowx=4:shadowy=4:shadowcolor=black@0.28"
        )
    if duration_s > 2:
        filters.append("fade=t=in:st=0:d=0.18")
        filters.append(f"fade=t=out:st={max(0.0, duration_s - 0.18):.2f}:d=0.18")

    if dialogue_speaker and dialogue_line:
        bubble_file = caption_dir / f"scene_{scene_index:02d}_bubble_{block_index:02d}.txt"
        clean_dialogue = trim_caption(dialogue_line, max_len=120)
        bubble_text = wrap_text(clean_dialogue, width=18, max_lines=3)
        bubble_file.write_text(bubble_text + "\n", encoding="utf-8")
        bubble_x, bubble_y = dialogue_bubble_position(scene_index, block_index)
        bubble_box_x = bubble_x.replace("w", "iw")
        shout = is_action_shout(dialogue_line)
        shape_font = resolve_shape_font() or font_file
        shape_font_expr = f"fontfile='{shape_font}':" if shape_font else ""
        shape_file = caption_dir / f"scene_{scene_index:02d}_bubble_shape_{block_index:02d}.txt"
        bubble_w = 660
        bubble_h = 250
        outline = 8
        body_w = bubble_w - bubble_h
        if shout:
            shape_file.write_text("✹\n", encoding="utf-8")
            filters.append(
                "drawtext="
                f"{shape_font_expr}"
                f"textfile='{shape_file.as_posix()}':"
                f"x={bubble_x}-6:y={bubble_y}-40:"
                "fontsize=420:fontcolor=0x111111@0.95:"
                "box=0:borderw=0:shadowx=0:shadowy=0"
            )
            filters.append(
                "drawtext="
                f"{shape_font_expr}"
                f"textfile='{shape_file.as_posix()}':"
                f"x={bubble_x}+10:y={bubble_y}-24:"
                "fontsize=390:fontcolor=0xFFF2A8@0.98:"
                "box=0:borderw=0:shadowx=0:shadowy=0"
            )
            filters.append(
                "drawtext="
                f"{font_expr}"
                f"textfile='{bubble_file.as_posix()}':"
                f"x={bubble_x}+78:y={bubble_y}+108:"
                "fontsize=34:fontcolor=0x111111:line_spacing=6:"
                "box=0:borderw=0:shadowx=0:shadowy=0"
            )
        else:
            shape_file.write_text("⬤\n", encoding="utf-8")
            filters.append(
                "drawbox="
                f"x={bubble_box_x}+{bubble_h // 2 - outline}:"
                f"y={bubble_y}-{outline}:"
                f"w={body_w + (2 * outline)}:h={bubble_h + (2 * outline)}:"
                "color=0x111111@0.95:t=fill"
            )
            filters.append(
                "drawbox="
                f"x={bubble_box_x}+{bubble_h // 2}:"
                f"y={bubble_y}:"
                f"w={body_w}:h={bubble_h}:"
                "color=white@0.97:t=fill"
            )
            filters.append(
                "drawtext="
                f"{shape_font_expr}"
                f"textfile='{shape_file.as_posix()}':"
                f"x={bubble_x}-{outline + 8}:y={bubble_y}-{outline + 13}:"
                f"fontsize={bubble_h + 38}:fontcolor=0x111111@0.95:"
                "box=0:borderw=0:shadowx=0:shadowy=0"
            )
            filters.append(
                "drawtext="
                f"{shape_font_expr}"
                f"textfile='{shape_file.as_posix()}':"
                f"x={bubble_x}+{body_w - outline - 8}:y={bubble_y}-{outline + 13}:"
                f"fontsize={bubble_h + 38}:fontcolor=0x111111@0.95:"
                "box=0:borderw=0:shadowx=0:shadowy=0"
            )
            filters.append(
                "drawtext="
                f"{shape_font_expr}"
                f"textfile='{shape_file.as_posix()}':"
                f"x={bubble_x}+4:y={bubble_y}-5:"
                f"fontsize={bubble_h + 18}:fontcolor=white@0.97:"
                "box=0:borderw=0:shadowx=0:shadowy=0"
            )
            filters.append(
                "drawtext="
                f"{shape_font_expr}"
                f"textfile='{shape_file.as_posix()}':"
                f"x={bubble_x}+{body_w + 4}:y={bubble_y}-5:"
                f"fontsize={bubble_h + 18}:fontcolor=white@0.97:"
                "box=0:borderw=0:shadowx=0:shadowy=0"
            )
            filters.append(
                "drawtext="
                f"{font_expr}"
                f"textfile='{bubble_file.as_posix()}':"
                f"x={bubble_x}+82:y={bubble_y}+92:"
                "fontsize=34:fontcolor=0x111111:line_spacing=6:"
                "box=0:borderw=0:shadowx=0:shadowy=0"
            )

    return ",".join(filters)


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


def scene_caption_blocks(scene: dict) -> list[dict]:
    raw_blocks = scene.get("caption_blocks")
    if isinstance(raw_blocks, list) and raw_blocks:
        output: list[dict] = []
        for idx, block in enumerate(raw_blocks, start=1):
            block_text = trim_caption(normalize_terminal_punctuation(str(block.get("text", ""))), max_len=150)
            if not block_text:
                continue
            dialogue_line = trim_caption(
                normalize_terminal_punctuation(str(block.get("dialogue_line", ""))),
                max_len=120,
            )
            output.append(
                {
                    "block_index": int(block.get("block_index", idx)),
                    "text": block_text,
                    "paragraph_index": int(block.get("paragraph_index", 0)),
                    "paragraph_block_index": int(block.get("paragraph_block_index", idx - 1)),
                    "paragraph_blocks_total": int(block.get("paragraph_blocks_total", 1)),
                    "duration_seconds": int(block.get("duration_seconds", 1)),
                    "image_path": str(block.get("image_path", scene.get("image_path", ""))),
                    "dialogue_speaker": normalize_ws(str(block.get("dialogue_speaker", ""))),
                    "dialogue_line": dialogue_line,
                }
            )
        if output:
            return output

    fallback_text = str(scene.get("narration", "")).strip()
    fallback_blocks = split_caption_blocks(fallback_text, max_blocks=3) or [fallback_text]
    if fallback_blocks and isinstance(fallback_blocks[0], str):
        fallback_blocks = [
            {
                "text": str(value),
                "paragraph_index": 0,
                "paragraph_block_index": idx,
                "paragraph_blocks_total": len(fallback_blocks),
            }
            for idx, value in enumerate(fallback_blocks)
        ]
    fallback_durations = split_duration_slots(int(scene.get("estimated_seconds", 1)), len(fallback_blocks))
    fallback_image = str(scene.get("image_path", ""))
    return [
        {
            "block_index": idx,
            "text": str(block["text"]),
            "paragraph_index": int(block.get("paragraph_index", 0)),
            "paragraph_block_index": int(block.get("paragraph_block_index", idx - 1)),
            "paragraph_blocks_total": int(block.get("paragraph_blocks_total", len(fallback_blocks))),
            "duration_seconds": int(fallback_durations[idx - 1]),
            "image_path": fallback_image,
            "dialogue_speaker": "",
            "dialogue_line": "",
        }
        for idx, block in enumerate(fallback_blocks, start=1)
    ]


def build_block_segment(
    ffmpeg: str,
    scene_index: int,
    block_index: int,
    paragraph_index: int,
    block_text: str,
    dialogue_speaker: str,
    dialogue_line: str,
    tmp_episode_dir: Path,
    image_path: Path,
    audio_path: Path,
    output_segment: Path,
    width: int,
    height: int,
    fps: int,
    duration_s: int,
    audio_start_s: float,
    font_file: str | None,
) -> None:
    vf = build_block_filter(
        scene_index=scene_index,
        block_index=block_index,
        paragraph_index=paragraph_index,
        block_text=block_text,
        dialogue_speaker=dialogue_speaker,
        dialogue_line=dialogue_line,
        tmp_episode_dir=tmp_episode_dir,
        width=width,
        height=height,
        fps=fps,
        duration_s=duration_s,
        font_file=font_file,
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
        "-ss",
        f"{audio_start_s:.3f}",
        "-t",
        str(duration_s),
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
    parser.add_argument(
        "--font-file",
        default=os.getenv("VIDEO_COMIC_FONT_FILE"),
        help="Optional TTF path for comic text. Defaults to VIDEO_COMIC_FONT_FILE env.",
    )
    parser.add_argument("--no-burn-subtitles", action="store_true", help="Do not burn subtitles into final MP4")
    args = parser.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ERROR: ffmpeg not found. Install ffmpeg first.")
        return 1

    try:
        font_file = resolve_font_file(args.font_file)
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
        captions_dir = tmp_episode_dir / "captions"
        if captions_dir.exists():
            shutil.rmtree(captions_dir)
        concat_list = tmp_episode_dir / "concat.txt"
        merged_video = tmp_episode_dir / "merged_no_subs.mp4"

        concat_lines: list[str] = []
        for scene in manifest_scenes:
            idx = int(scene["scene_index"])
            audio_path = Path(str(scene["audio_path"]))
            if not audio_path.exists():
                raise RuntimeError(f"Missing audio asset: {audio_path}")

            caption_blocks = scene_caption_blocks(scene)
            slot_sum = sum(int(block["duration_seconds"]) for block in caption_blocks)
            target_duration = int(scene.get("estimated_seconds", slot_sum))
            if slot_sum != target_duration and caption_blocks:
                caption_blocks[-1]["duration_seconds"] = int(caption_blocks[-1]["duration_seconds"]) + (target_duration - slot_sum)

            audio_cursor = 0.0
            for block in caption_blocks:
                block_index = int(block["block_index"])
                block_duration = max(1, int(block["duration_seconds"]))
                paragraph_index = int(block.get("paragraph_index", 0))
                image_path = Path(str(block["image_path"]))
                if not image_path.exists():
                    raise RuntimeError(f"Missing image asset: {image_path}")
                segment_path = tmp_episode_dir / f"segment_{idx:02d}_{block_index:02d}.mp4"
                build_block_segment(
                    ffmpeg=ffmpeg,
                    scene_index=idx,
                    block_index=block_index,
                    paragraph_index=paragraph_index,
                    block_text=str(block["text"]),
                    dialogue_speaker=str(block.get("dialogue_speaker", "")),
                    dialogue_line=str(block.get("dialogue_line", "")),
                    tmp_episode_dir=tmp_episode_dir,
                    image_path=image_path,
                    audio_path=audio_path,
                    output_segment=segment_path,
                    width=args.width,
                    height=args.height,
                    fps=args.fps,
                    duration_s=block_duration,
                    audio_start_s=audio_cursor,
                    font_file=font_file,
                )
                concat_lines.append(f"file '{segment_path.as_posix()}'")
                audio_cursor += float(block_duration)

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
        print(f"Comic font: {font_file or 'system default'}")
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: ffmpeg command failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
