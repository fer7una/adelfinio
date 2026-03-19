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
from functools import lru_cache
from pathlib import Path

try:
    from video_text_layout import TEXT_FIT_CHAR_WIDTH_FACTOR, wrap_text_unbounded
except ModuleNotFoundError:  # Support package-style imports in local tooling.
    from scripts.video_text_layout import TEXT_FIT_CHAR_WIDTH_FACTOR, wrap_text_unbounded

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ASSETS_DIR = ROOT / "artifacts" / "scene_assets"
DEFAULT_VIDEO_DIR = ROOT / "artifacts" / "videos" / "final"
DEFAULT_SUBS_DIR = ROOT / "artifacts" / "subtitles" / "final"
DEFAULT_OVERLAY_DIR = ROOT / "assets" / "video_overlays"
TMP_DIR = ROOT / ".tmp" / "compose_final"
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


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid JSON payload: {path}")
    return payload


def fmt_srt_time(seconds: float) -> str:
    total_ms = max(0, round(float(seconds) * 1000))
    h = total_ms // 3_600_000
    m = (total_ms % 3_600_000) // 60_000
    s = (total_ms % 60_000) // 1000
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


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


def normalize_terminal_punctuation(text: str) -> str:
    clean = normalize_ws(str(text or "").translate(SMART_PUNCT_TRANSLATION))
    clean = re.sub(r"\s+([,.;:!?])", r"\1", clean)
    clean = re.sub(r"([,.;:!?])(?![\s\"')\]])", r"\1 ", clean)
    clean = re.sub(r"\s*\.\s*\.\s*\.", "...", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    clean = re.sub(r"^\.\.\.\s+(\S)", r"...\1", clean)
    capitalized = capitalize_sentence_starts(clean)
    original_ellipsis = re.match(r"^(\.\.\.)([A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ\-]*)", clean)
    capitalized_ellipsis = re.match(r"^(\.\.\.)([A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ\-]*)", capitalized)
    if original_ellipsis and capitalized_ellipsis:
        start = capitalized_ellipsis.start(2)
        end = capitalized_ellipsis.end(2)
        capitalized = capitalized[:start] + original_ellipsis.group(2) + capitalized[end:]
    return capitalized


def lowercase_after_ellipsis_if_needed(text: str, proper_nouns: set[str] | None = None) -> str:
    clean = normalize_terminal_punctuation(text)
    if not clean.startswith("... "):
        return clean
    proper_nouns = {normalize_ws(item).lower() for item in (proper_nouns or set()) if normalize_ws(item)}
    match = re.match(r"^\.\.\.\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ\-]*)", clean)
    if not match:
        return clean
    word = match.group(1)
    if normalize_ws(word).lower() in proper_nouns:
        return clean
    start = match.start(1)
    return clean[:start] + word[:1].lower() + word[1:] + clean[match.end(1):]


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


def fit_wrapped_text(
    text: str,
    box_w: int,
    box_h: int,
    *,
    max_font_size: int,
    min_font_size: int,
    max_lines: int,
    char_width_factor: float = TEXT_FIT_CHAR_WIDTH_FACTOR,
) -> tuple[str, int, int]:
    clean = normalize_ws(text)
    if not clean:
        return "", min_font_size, max(4, round(min_font_size * 0.16))

    for font_size in range(max_font_size, min_font_size - 1, -1):
        line_spacing = max(4, round(font_size * 0.16))
        max_chars = max(8, int(box_w / max(1.0, font_size * char_width_factor)))
        lines = wrap_text_unbounded(clean, max_chars)
        if not lines or len(lines) > max_lines:
            continue
        wrapped = "\n".join(lines)
        est_width = max(len(line) for line in lines) * font_size * char_width_factor
        est_height = (len(lines) * font_size) + ((len(lines) - 1) * line_spacing)
        if est_width <= box_w and est_height <= box_h:
            return wrapped, font_size, line_spacing

    fallback_spacing = max(4, round(min_font_size * 0.16))
    fallback_chars = max(8, int(box_w / max(1.0, min_font_size * char_width_factor)))
    return wrap_text(clean, width=fallback_chars, max_lines=max_lines), min_font_size, fallback_spacing


def sanitize_display_narration(text: str) -> str:
    raw = normalize_ws(text)
    if not raw:
        return ""

    if ":" in raw:
        prefix, suffix = raw.split(":", 1)
        low_prefix = normalize_ws(prefix).lower()
        theatrical_markers = (
            "entra en escena",
            "sale a escena",
            "aparece en escena",
            "irrumpe en escena",
        )
        if any(marker in low_prefix for marker in theatrical_markers):
            return normalize_terminal_punctuation(suffix)
    return normalize_terminal_punctuation(raw)


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


def resolve_narration_font_file(explicit_font_file: str | None) -> str | None:
    if explicit_font_file:
        candidate = Path(explicit_font_file)
        if candidate.exists():
            stem = candidate.stem
            parent = candidate.parent
            italic_candidates = [
                parent / f"{stem.replace('Bold', 'Italic')}{candidate.suffix}",
                parent / f"{stem.replace('Regular', 'Italic')}{candidate.suffix}",
                parent / f"{stem}-Italic{candidate.suffix}",
            ]
            for item in italic_candidates:
                if item.exists():
                    return item.as_posix()

    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
        "/usr/share/fonts/opentype/urw-base35/NimbusRoman-Italic.otf",
        "/usr/share/fonts/truetype/liberation2/LiberationSerif-Italic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    ]
    for item in candidates:
        if Path(item).exists():
            return item
    return explicit_font_file


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


def centered_overlay_position(
    frame_w: int,
    frame_h: int,
    overlay_w: int,
    overlay_h: int,
    *,
    vertical_anchor: str,
) -> tuple[str, str]:
    if vertical_anchor == "upper_third":
        center_y = frame_h / 6
    elif vertical_anchor == "lower_third":
        center_y = frame_h * (5 / 6)
    else:
        raise ValueError(f"Unsupported vertical anchor: {vertical_anchor}")

    overlay_x = max(0, round((frame_w - overlay_w) / 2))
    overlay_y = max(0, min(frame_h - overlay_h, round(center_y - (overlay_h / 2))))
    return str(overlay_x), str(overlay_y)


def anchored_overlay_position(
    frame_w: int,
    frame_h: int,
    overlay_w: int,
    overlay_h: int,
    *,
    horizontal_anchor: str,
    vertical_anchor: str,
) -> tuple[str, str]:
    if horizontal_anchor == "left":
        overlay_x = 42
    elif horizontal_anchor == "right":
        overlay_x = max(0, frame_w - overlay_w - 42)
    else:
        overlay_x = max(0, round((frame_w - overlay_w) / 2))

    if vertical_anchor == "upper_third":
        center_y = frame_h / 6
    elif vertical_anchor == "lower_third":
        center_y = frame_h * (5 / 6)
    else:
        raise ValueError(f"Unsupported vertical anchor: {vertical_anchor}")

    overlay_y = max(18, min(frame_h - overlay_h, round(center_y - (overlay_h / 2))))
    return str(overlay_x), str(overlay_y)


def centered_text_position(
    overlay_x: str,
    overlay_y: str,
    *,
    overlay_w: int,
    overlay_h: int,
    pad_x: int,
    pad_y: int,
) -> tuple[str, str, int, int]:
    text_box_w = max(80, overlay_w - (2 * pad_x))
    text_box_h = max(40, overlay_h - (2 * pad_y))
    text_x = f"{overlay_x}+{pad_x}+({text_box_w}-text_w)/2"
    text_y = f"{overlay_y}+{pad_y}+({text_box_h}-text_h)/2"
    return text_x, text_y, text_box_w, text_box_h


def narration_paddings(overlay_w: int, overlay_h: int, *, compact: bool = False) -> tuple[int, int, int, int]:
    left_pad = max(78, round(overlay_w * 0.14))
    right_pad = max(78, round(overlay_w * 0.14))
    top_pad = max(26, round(overlay_h * 0.12))
    bottom_pad = max(74, round(overlay_h * 0.26))
    if compact:
        left_pad = max(52, round(left_pad * 0.78))
        right_pad = max(52, round(right_pad * 0.78))
        top_pad = max(18, round(top_pad * 0.82))
        bottom_pad = max(52, round(bottom_pad * 0.78))
    return left_pad, right_pad, top_pad, bottom_pad


def narration_layout(
    frame_w: int,
    frame_h: int,
    overlay_w: int,
    overlay_h: int,
    *,
    compact: bool = False,
) -> tuple[str, str, str, str, int, int]:
    overlay_x, overlay_y = centered_overlay_position(
        frame_w,
        frame_h,
        overlay_w,
        overlay_h,
        vertical_anchor="lower_third",
    )
    left_pad, right_pad, top_pad, bottom_pad = narration_paddings(overlay_w, overlay_h, compact=compact)
    text_box_w = max(120, overlay_w - left_pad - right_pad)
    text_box_h = max(60, overlay_h - top_pad - bottom_pad)
    text_x = f"{overlay_x}+{left_pad}+({text_box_w}-text_w)/2"
    text_y = f"{overlay_y}+{top_pad}+({text_box_h}-text_h)/2"
    return overlay_x, overlay_y, text_x, text_y, text_box_w, text_box_h


def narration_text_box(
    overlay_w: int,
    overlay_h: int,
    *,
    compact: bool = False,
) -> tuple[str, str, int, int]:
    left_pad, right_pad, top_pad, bottom_pad = narration_paddings(overlay_w, overlay_h, compact=compact)
    text_box_w = max(120, overlay_w - left_pad - right_pad)
    text_box_h = max(60, overlay_h - top_pad - bottom_pad)
    text_x = f"{left_pad}+({text_box_w}-text_w)/2"
    text_y = f"{top_pad}+({text_box_h}-text_h)/2"
    return text_x, text_y, text_box_w, text_box_h


def dialogue_layout(
    frame_w: int,
    frame_h: int,
    overlay_w: int,
    overlay_h: int,
    *,
    shout: bool,
    bubble_anchor: str,
) -> tuple[str, str, str, str, int, int]:
    horizontal_anchor = "left" if bubble_anchor == "upper_left" else "right" if bubble_anchor == "upper_right" else "center"
    overlay_x, overlay_y = anchored_overlay_position(
        frame_w,
        frame_h,
        overlay_w,
        overlay_h,
        horizontal_anchor=horizontal_anchor,
        vertical_anchor="upper_third",
    )
    pad_x = max(92, round(overlay_w * (0.24 if shout else 0.22)))
    pad_y = max(74, round(overlay_h * (0.27 if shout else 0.24)))
    text_x, text_y, text_box_w, text_box_h = centered_text_position(
        overlay_x,
        overlay_y,
        overlay_w=overlay_w,
        overlay_h=overlay_h,
        pad_x=pad_x,
        pad_y=pad_y,
    )
    return overlay_x, overlay_y, text_x, text_y, text_box_w, text_box_h


def bbox_to_pixels(
    bbox: list[float] | None,
    *,
    frame_w: int,
    frame_h: int,
    fallback_w: int,
    fallback_h: int,
) -> tuple[int, int, int, int]:
    if not isinstance(bbox, list) or len(bbox) != 4:
        x = max(0, round((frame_w - fallback_w) / 2))
        y = max(0, round((frame_h - fallback_h) / 2))
        return x, y, fallback_w, fallback_h
    x = max(0, min(frame_w - 1, round(float(bbox[0]) * frame_w)))
    y = max(0, min(frame_h - 1, round(float(bbox[1]) * frame_h)))
    w = max(1, min(frame_w - x, round(float(bbox[2]) * frame_w)))
    h = max(1, min(frame_h - y, round(float(bbox[3]) * frame_h)))
    return x, y, w, h


def fit_overlay_size_within_box(
    *,
    svg_path: Path | None,
    target_box_w: int,
    target_box_h: int,
    fallback_native_w: int,
    fallback_native_h: int,
) -> tuple[int, int]:
    canvas = svg_canvas_size(svg_path.as_posix()) if svg_path else None
    native_w = fallback_native_w
    native_h = fallback_native_h
    if canvas and canvas[0] > 0 and canvas[1] > 0:
        native_w, native_h = round(canvas[0]), round(canvas[1])
    scale = min(target_box_w / max(1, native_w), target_box_h / max(1, native_h))
    return max(1, round(native_w * scale)), max(1, round(native_h * scale))


def narration_layout_from_box(
    *,
    overlay_x: int,
    overlay_y: int,
    overlay_w: int,
    overlay_h: int,
    compact: bool = False,
) -> tuple[str, str, str, str, int, int]:
    left_pad, right_pad, top_pad, bottom_pad = narration_paddings(overlay_w, overlay_h, compact=compact)
    text_box_w = max(120, overlay_w - left_pad - right_pad)
    text_box_h = max(60, overlay_h - top_pad - bottom_pad)
    text_x = f"{overlay_x}+{left_pad}+({text_box_w}-text_w)/2"
    text_y = f"{overlay_y}+{top_pad}+({text_box_h}-text_h)/2"
    return str(overlay_x), str(overlay_y), text_x, text_y, text_box_w, text_box_h


def dialogue_layout_from_box(
    *,
    overlay_x: int,
    overlay_y: int,
    overlay_w: int,
    overlay_h: int,
    shout: bool,
) -> tuple[str, str, str, str, int, int]:
    pad_x = max(92, round(overlay_w * (0.24 if shout else 0.22)))
    pad_y = max(74, round(overlay_h * (0.27 if shout else 0.24)))
    text_x, text_y, text_box_w, text_box_h = centered_text_position(
        str(overlay_x),
        str(overlay_y),
        overlay_w=overlay_w,
        overlay_h=overlay_h,
        pad_x=pad_x,
        pad_y=pad_y,
    )
    return str(overlay_x), str(overlay_y), text_x, text_y, text_box_w, text_box_h


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


def escape_filter_path(path: Path | str) -> str:
    value = str(path).replace("\\", "/")
    return value.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")


def resolve_overlay_assets(overlay_assets_dir: str | None) -> dict[str, Path | None]:
    base_dir = Path(overlay_assets_dir) if overlay_assets_dir else DEFAULT_OVERLAY_DIR
    assets: dict[str, Path | None] = {}
    for key in ("narration", "dialogue", "shout"):
        candidate = base_dir / f"{key}.svg"
        assets[key] = candidate if candidate.exists() else None
    return assets


@lru_cache(maxsize=None)
def svg_canvas_size(svg_path: str) -> tuple[float, float] | None:
    try:
        text = Path(svg_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    viewbox_match = re.search(r'viewBox="([^"]+)"', text)
    if viewbox_match:
        parts = [p for p in re.split(r"[,\s]+", viewbox_match.group(1).strip()) if p]
        if len(parts) == 4:
            try:
                return float(parts[2]), float(parts[3])
            except ValueError:
                pass

    width_match = re.search(r'width="([0-9.]+)', text)
    height_match = re.search(r'height="([0-9.]+)', text)
    if width_match and height_match:
        try:
            return float(width_match.group(1)), float(height_match.group(1))
        except ValueError:
            return None
    return None


def scaled_svg_size(svg_path: Path, target_width: int, fallback_height: int) -> tuple[int, int]:
    canvas = svg_canvas_size(svg_path.as_posix())
    if not canvas:
        return target_width, fallback_height
    canvas_w, canvas_h = canvas
    if canvas_w <= 0 or canvas_h <= 0:
        return target_width, fallback_height
    scaled_h = max(1, round(target_width * (canvas_h / canvas_w)))
    return target_width, scaled_h


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
        out.append(
            {
                "speaker": speaker,
                "line": line,
                "delivery": normalize_dialogue_delivery(str(item.get("delivery", "")), line),
            }
        )
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
    dialogue_delivery: str,
    focus_anchor: str = "upper_center",
    bubble_anchor: str = "",
    overlay_assets: dict[str, Path | None] | None = None,
    scene_duration_s: int | None = None,
    phase_start_s: float = 0.0,
    phase_end_s: float | None = None,
    focus_bbox: list[float] | None = None,
    overlay_bbox: list[float] | None = None,
) -> str:
    overlay_assets = overlay_assets or {}
    frames = max(1, duration_s * fps)
    total_scene_s = max(duration_s, int(scene_duration_s or duration_s))
    phase_end_s = float(phase_end_s if phase_end_s is not None else (phase_start_s + duration_s))
    progress_start = max(0.0, min(1.0, float(phase_start_s) / total_scene_s))
    progress_end = max(progress_start, min(1.0, float(phase_end_s) / total_scene_s))
    if isinstance(focus_bbox, list) and len(focus_bbox) == 4:
        center_x = max(0.0, min(1.0, float(focus_bbox[0]) + (float(focus_bbox[2]) / 2)))
        center_y = max(0.0, min(1.0, float(focus_bbox[1]) + (float(focus_bbox[3]) / 2)))
        zoom_start = 1.03 + (0.24 * progress_start)
        zoom_end = 1.03 + (0.24 * progress_end)
        zoom_step = 0.0 if frames <= 1 else max(0.0, (zoom_end - zoom_start) / frames)
        zoom_expr = f"if(lte(on,1),{zoom_start:.5f},min({zoom_end:.5f},{zoom_start:.5f}+on*{zoom_step:.6f}))"
    else:
        zoom_expr = "if(lte(on,1),1.03,min(1.03+on*0.0018,1.28))"
        focus_map = {
            "upper_left": (0.35, 0.34),
            "upper_center": (0.50, 0.33),
            "upper_right": (0.65, 0.34),
        }
        center_x, center_y = focus_map.get(focus_anchor, focus_map["upper_center"])
    x_expr = f"min(max(iw*{center_x:.4f}-(iw/zoom/2),0),iw-iw/zoom)"
    y_expr = f"min(max(ih*{center_y:.4f}-(ih/zoom/2),0),ih-ih/zoom)"

    filters: list[str] = [
        f"scale={width}:{height}:force_original_aspect_ratio=increase",
        f"crop={width}:{height}",
        (
            f"zoompan=z='{zoom_expr}':"
            f"x='{x_expr}':"
            f"y='{y_expr}':"
            f"d={frames}:s={width}x{height}:fps={fps}"
        ),
        "eq=contrast=1.08:saturation=1.36:brightness=0.01",
        "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.10:t=22",
        "format=rgba",
    ]
    graph: list[str] = [f"[0:v]{','.join(filters)}[v0]"]
    current_label = "v0"
    stage_idx = 1

    caption_dir = tmp_episode_dir / "captions"
    caption_dir.mkdir(parents=True, exist_ok=True)
    caption_file = caption_dir / f"scene_{scene_index:02d}_block_{block_index:02d}.txt"
    clean_block_text = sanitize_display_narration(block_text)
    escaped_font_file = escape_filter_path(font_file) if font_file else None
    font_expr = f"fontfile='{escaped_font_file}':" if escaped_font_file else ""
    narration_font_file = resolve_narration_font_file(font_file)
    escaped_narration_font = escape_filter_path(narration_font_file) if narration_font_file else None
    narration_font_expr = f"fontfile='{escaped_narration_font}':" if escaped_narration_font else font_expr
    dialogue_mode = bool(dialogue_line)
    dialogue_delivery = normalize_dialogue_delivery(dialogue_delivery, dialogue_line)
    if not dialogue_mode:
        narration_svg = overlay_assets.get("narration")
        if narration_svg:
            if isinstance(overlay_bbox, list) and len(overlay_bbox) == 4:
                box_x, box_y, box_w, box_h = bbox_to_pixels(
                    overlay_bbox,
                    frame_w=width,
                    frame_h=height,
                    fallback_w=900,
                    fallback_h=310,
                )
                overlay_w, overlay_h = fit_overlay_size_within_box(
                    svg_path=narration_svg,
                    target_box_w=box_w,
                    target_box_h=box_h,
                    fallback_native_w=900,
                    fallback_native_h=310,
                )
                overlay_x_px = box_x + max(0, round((box_w - overlay_w) / 2))
                overlay_y_px = box_y + max(0, round((box_h - overlay_h) / 2))
                overlay_x, overlay_y, text_x, text_y, text_box_w, text_box_h = narration_layout_from_box(
                    overlay_x=overlay_x_px,
                    overlay_y=overlay_y_px,
                    overlay_w=overlay_w,
                    overlay_h=overlay_h,
                )
                local_text_x, local_text_y, _, _ = narration_text_box(overlay_w=overlay_w, overlay_h=overlay_h)
            else:
                overlay_w, overlay_h = scaled_svg_size(narration_svg, target_width=900, fallback_height=310)
                overlay_x, overlay_y, text_x, text_y, text_box_w, text_box_h = narration_layout(
                    frame_w=width,
                    frame_h=height,
                    overlay_w=overlay_w,
                    overlay_h=overlay_h,
                )
                local_text_x, local_text_y, _, _ = narration_text_box(
                    overlay_w=overlay_w,
                    overlay_h=overlay_h,
                )
            wrapped_text, font_size, line_spacing = fit_wrapped_text(
                clean_block_text,
                text_box_w,
                text_box_h,
                max_font_size=42,
                min_font_size=26,
                max_lines=3,
                char_width_factor=TEXT_FIT_CHAR_WIDTH_FACTOR,
            )
            if font_size <= 32:
                if isinstance(overlay_bbox, list) and len(overlay_bbox) == 4:
                    overlay_x, overlay_y, text_x, text_y, text_box_w, text_box_h = narration_layout_from_box(
                        overlay_x=overlay_x_px,
                        overlay_y=overlay_y_px,
                        overlay_w=overlay_w,
                        overlay_h=overlay_h,
                        compact=True,
                    )
                else:
                    overlay_x, overlay_y, text_x, text_y, text_box_w, text_box_h = narration_layout(
                        frame_w=width,
                        frame_h=height,
                        overlay_w=overlay_w,
                        overlay_h=overlay_h,
                        compact=True,
                    )
                local_text_x, local_text_y, _, _ = narration_text_box(
                    overlay_w=overlay_w,
                    overlay_h=overlay_h,
                    compact=True,
                )
                wrapped_text, font_size, line_spacing = fit_wrapped_text(
                    clean_block_text,
                    text_box_w,
                    text_box_h,
                    max_font_size=42,
                    min_font_size=26,
                    max_lines=3,
                    char_width_factor=TEXT_FIT_CHAR_WIDTH_FACTOR,
                )
            caption_file.write_text(wrapped_text + "\n", encoding="utf-8")
            overlay_label = f"narr_{block_index:02d}"
            next_label = f"v{stage_idx}"
            graph.append(
                f"movie='{escape_filter_path(narration_svg)}',scale={overlay_w}:{overlay_h}:flags=lanczos[{overlay_label}]"
            )
            graph.append(
                f"[{current_label}][{overlay_label}]overlay={overlay_x}:{overlay_y}:format=auto[{next_label}]"
            )
            current_label = next_label
            stage_idx += 1
            text_base_label = f"narr_text_base_{block_index:02d}"
            graph.append(
                f"color=c=black@0.0:s={overlay_w}x{overlay_h}:d={duration_s},format=rgba[{text_base_label}]"
            )
            text_label = f"narr_text_{block_index:02d}"
            graph.append(
                f"[{text_base_label}]drawtext="
                f"{narration_font_expr}"
                f"textfile='{escape_filter_path(caption_file)}':"
                f"x={local_text_x}:y={local_text_y}:"
                f"fontsize={font_size}:fontcolor=0x111111:line_spacing={line_spacing}:"
                "box=0:borderw=0:shadowx=0:shadowy=0"
                f"[{text_label}]"
            )
            skew_label = f"narr_text_skew_{block_index:02d}"
            graph.append(
                f"[{text_label}]shear=shx=0.25:shy=0.0:fillcolor=black@0.0[{skew_label}]"
            )
            next_label = f"v{stage_idx}"
            graph.append(
                f"[{current_label}][{skew_label}]overlay={overlay_x}:{overlay_y}:format=auto[{next_label}]"
            )
            current_label = next_label
            stage_idx += 1
        else:
            if isinstance(overlay_bbox, list) and len(overlay_bbox) == 4:
                box_x, box_y, box_w, box_h = bbox_to_pixels(
                    overlay_bbox,
                    frame_w=width,
                    frame_h=height,
                    fallback_w=760,
                    fallback_h=180,
                )
                text_x = f"{box_x}+({box_w}-text_w)/2"
                text_y = f"{box_y}+({box_h}-text_h)/2"
                fit_box_w = box_w
                fit_box_h = box_h
            else:
                text_x = "(w-text_w)/2"
                text_y = "h-h/6-text_h/2"
                fit_box_w = 760
                fit_box_h = 180
            wrapped_text, font_size, line_spacing = fit_wrapped_text(
                clean_block_text,
                box_w=fit_box_w,
                box_h=fit_box_h,
                max_font_size=42,
                min_font_size=26,
                max_lines=3,
                char_width_factor=TEXT_FIT_CHAR_WIDTH_FACTOR,
            )
            if font_size <= 32 and not (isinstance(overlay_bbox, list) and len(overlay_bbox) == 4):
                fit_box_w = 840
                fit_box_h = 210
                text_x = "(w-text_w)/2"
                text_y = "h-h/6-text_h/2"
                wrapped_text, font_size, line_spacing = fit_wrapped_text(
                    clean_block_text,
                    box_w=fit_box_w,
                    box_h=fit_box_h,
                    max_font_size=42,
                    min_font_size=26,
                    max_lines=3,
                    char_width_factor=TEXT_FIT_CHAR_WIDTH_FACTOR,
                )
            caption_file.write_text(wrapped_text + "\n", encoding="utf-8")
            next_label = f"v{stage_idx}"
            graph.append(
                f"[{current_label}]drawtext="
                f"{narration_font_expr}"
                f"textfile='{escape_filter_path(caption_file)}':"
                f"x={text_x}:y={text_y}:"
                f"fontsize={font_size}:fontcolor=0x111111:line_spacing={line_spacing}:"
                "box=1:boxcolor=white@0.97:boxborderw=16:"
                "borderw=1:bordercolor=0x111111@0.92:"
                "shadowx=4:shadowy=4:shadowcolor=black@0.28"
                f"[{next_label}]"
            )
            current_label = next_label
            stage_idx += 1

    if dialogue_speaker and dialogue_line:
        bubble_file = caption_dir / f"scene_{scene_index:02d}_bubble_{block_index:02d}.txt"
        clean_dialogue = normalize_terminal_punctuation(block_text or dialogue_line)
        shout = dialogue_delivery == "shout"
        overlay_key = "shout" if shout else "dialogue"
        bubble_svg = overlay_assets.get(overlay_key)
        shape_font = resolve_shape_font() or font_file
        escaped_shape_font = escape_filter_path(shape_font) if shape_font else None
        shape_font_expr = f"fontfile='{escaped_shape_font}':" if escaped_shape_font else ""
        shape_file = caption_dir / f"scene_{scene_index:02d}_bubble_shape_{block_index:02d}.txt"
        bubble_w = 680
        bubble_h = 260
        outline = 8
        body_w = bubble_w - bubble_h
        if bubble_svg:
            if isinstance(overlay_bbox, list) and len(overlay_bbox) == 4:
                box_x, box_y, box_w, box_h = bbox_to_pixels(
                    overlay_bbox,
                    frame_w=width,
                    frame_h=height,
                    fallback_w=bubble_w,
                    fallback_h=bubble_h,
                )
                overlay_w, overlay_h = box_w, box_h
                overlay_x_px = box_x + max(0, round((box_w - overlay_w) / 2))
                overlay_y_px = box_y + max(0, round((box_h - overlay_h) / 2))
                bubble_x, bubble_y, text_x, text_y, text_box_w, text_box_h = dialogue_layout_from_box(
                    overlay_x=overlay_x_px,
                    overlay_y=overlay_y_px,
                    overlay_w=overlay_w,
                    overlay_h=overlay_h,
                    shout=shout,
                )
            else:
                overlay_w, overlay_h = scaled_svg_size(bubble_svg, target_width=bubble_w, fallback_height=bubble_h)
                bubble_x, bubble_y, text_x, text_y, text_box_w, text_box_h = dialogue_layout(
                    frame_w=width,
                    frame_h=height,
                    overlay_w=overlay_w,
                    overlay_h=overlay_h,
                    shout=shout,
                    bubble_anchor=bubble_anchor,
                )
            wrapped_text, font_size, line_spacing = fit_wrapped_text(
                clean_dialogue,
                text_box_w,
                text_box_h,
                max_font_size=36 if shout else 38,
                min_font_size=22,
                max_lines=3,
                char_width_factor=TEXT_FIT_CHAR_WIDTH_FACTOR,
            )
            bubble_file.write_text(wrapped_text + "\n", encoding="utf-8")
            overlay_label = f"{overlay_key}_{block_index:02d}"
            next_label = f"v{stage_idx}"
            graph.append(
                f"movie='{escape_filter_path(bubble_svg)}',scale={overlay_w}:{overlay_h}:flags=lanczos[{overlay_label}]"
            )
            graph.append(
                f"[{current_label}][{overlay_label}]overlay={bubble_x}:{bubble_y}:format=auto[{next_label}]"
            )
            current_label = next_label
            stage_idx += 1
            next_label = f"v{stage_idx}"
            graph.append(
                f"[{current_label}]drawtext="
                f"{font_expr}"
                f"textfile='{escape_filter_path(bubble_file)}':"
                f"x={text_x}:y={text_y}:"
                f"fontsize={font_size}:fontcolor=0x111111:line_spacing={line_spacing}:"
                "box=0:borderw=0:shadowx=0:shadowy=0"
                f"[{next_label}]"
            )
            current_label = next_label
            stage_idx += 1
        elif shout:
            if isinstance(overlay_bbox, list) and len(overlay_bbox) == 4:
                box_x, box_y, _, _ = bbox_to_pixels(
                    overlay_bbox,
                    frame_w=width,
                    frame_h=height,
                    fallback_w=bubble_w,
                    fallback_h=bubble_h,
                )
                bubble_x = str(box_x)
                bubble_y = str(box_y)
            else:
                bubble_x, bubble_y = anchored_overlay_position(
                    width,
                    height,
                    bubble_w,
                    bubble_h,
                    horizontal_anchor="left" if bubble_anchor == "upper_left" else "right" if bubble_anchor == "upper_right" else "center",
                    vertical_anchor="upper_third",
                )
            wrapped_text, font_size, line_spacing = fit_wrapped_text(
                clean_dialogue,
                box_w=320,
                box_h=150,
                max_font_size=36,
                min_font_size=22,
                max_lines=3,
                char_width_factor=TEXT_FIT_CHAR_WIDTH_FACTOR,
            )
            bubble_file.write_text(wrapped_text + "\n", encoding="utf-8")
            shape_file.write_text("✹\n", encoding="utf-8")
            next_label = f"v{stage_idx}"
            graph.append(
                f"[{current_label}]drawtext="
                f"{shape_font_expr}"
                f"textfile='{escape_filter_path(shape_file)}':"
                f"x={bubble_x}-6:y={bubble_y}-40:"
                "fontsize=420:fontcolor=0x111111@0.95:"
                "box=0:borderw=0:shadowx=0:shadowy=0"
                f"[{next_label}]"
            )
            current_label = next_label
            stage_idx += 1
            next_label = f"v{stage_idx}"
            graph.append(
                f"[{current_label}]drawtext="
                f"{shape_font_expr}"
                f"textfile='{escape_filter_path(shape_file)}':"
                f"x={bubble_x}+10:y={bubble_y}-24:"
                "fontsize=390:fontcolor=0xFFF2A8@0.98:"
                "box=0:borderw=0:shadowx=0:shadowy=0"
                f"[{next_label}]"
            )
            current_label = next_label
            stage_idx += 1
            next_label = f"v{stage_idx}"
            graph.append(
                f"[{current_label}]drawtext="
                f"{font_expr}"
                f"textfile='{escape_filter_path(bubble_file)}':"
                f"x={bubble_x}+({bubble_w}-text_w)/2:y={bubble_y}+({bubble_h}-text_h)/2:"
                f"fontsize={font_size}:fontcolor=0x111111:line_spacing={line_spacing}:"
                "box=0:borderw=0:shadowx=0:shadowy=0"
                f"[{next_label}]"
            )
            current_label = next_label
            stage_idx += 1
        else:
            if isinstance(overlay_bbox, list) and len(overlay_bbox) == 4:
                box_x, box_y, _, _ = bbox_to_pixels(
                    overlay_bbox,
                    frame_w=width,
                    frame_h=height,
                    fallback_w=bubble_w,
                    fallback_h=bubble_h,
                )
                bubble_x = str(box_x)
                bubble_y = str(box_y)
            else:
                bubble_x, bubble_y = anchored_overlay_position(
                    width,
                    height,
                    bubble_w,
                    bubble_h,
                    horizontal_anchor="left" if bubble_anchor == "upper_left" else "right" if bubble_anchor == "upper_right" else "center",
                    vertical_anchor="upper_third",
                )
            bubble_box_x = bubble_x.replace("w", "iw")
            wrapped_text, font_size, line_spacing = fit_wrapped_text(
                clean_dialogue,
                box_w=360,
                box_h=120,
                max_font_size=38,
                min_font_size=22,
                max_lines=3,
                char_width_factor=TEXT_FIT_CHAR_WIDTH_FACTOR,
            )
            bubble_file.write_text(wrapped_text + "\n", encoding="utf-8")
            shape_file.write_text("⬤\n", encoding="utf-8")
            next_label = f"v{stage_idx}"
            graph.append(
                f"[{current_label}]drawbox="
                f"x={bubble_box_x}+{bubble_h // 2 - outline}:"
                f"y={bubble_y}-{outline}:"
                f"w={body_w + (2 * outline)}:h={bubble_h + (2 * outline)}:"
                "color=0x111111@0.95:t=fill"
                f"[{next_label}]"
            )
            current_label = next_label
            stage_idx += 1
            next_label = f"v{stage_idx}"
            graph.append(
                f"[{current_label}]drawbox="
                f"x={bubble_box_x}+{bubble_h // 2}:"
                f"y={bubble_y}:"
                f"w={body_w}:h={bubble_h}:"
                "color=white@0.97:t=fill"
                f"[{next_label}]"
            )
            current_label = next_label
            stage_idx += 1
            next_label = f"v{stage_idx}"
            graph.append(
                f"[{current_label}]drawtext="
                f"{shape_font_expr}"
                f"textfile='{escape_filter_path(shape_file)}':"
                f"x={bubble_x}-{outline + 8}:y={bubble_y}-{outline + 13}:"
                f"fontsize={bubble_h + 38}:fontcolor=0x111111@0.95:"
                "box=0:borderw=0:shadowx=0:shadowy=0"
                f"[{next_label}]"
            )
            current_label = next_label
            stage_idx += 1
            next_label = f"v{stage_idx}"
            graph.append(
                f"[{current_label}]drawtext="
                f"{shape_font_expr}"
                f"textfile='{escape_filter_path(shape_file)}':"
                f"x={bubble_x}+{body_w - outline - 8}:y={bubble_y}-{outline + 13}:"
                f"fontsize={bubble_h + 38}:fontcolor=0x111111@0.95:"
                "box=0:borderw=0:shadowx=0:shadowy=0"
                f"[{next_label}]"
            )
            current_label = next_label
            stage_idx += 1
            next_label = f"v{stage_idx}"
            graph.append(
                f"[{current_label}]drawtext="
                f"{shape_font_expr}"
                f"textfile='{escape_filter_path(shape_file)}':"
                f"x={bubble_x}+4:y={bubble_y}-5:"
                f"fontsize={bubble_h + 18}:fontcolor=white@0.97:"
                "box=0:borderw=0:shadowx=0:shadowy=0"
                f"[{next_label}]"
            )
            current_label = next_label
            stage_idx += 1
            next_label = f"v{stage_idx}"
            graph.append(
                f"[{current_label}]drawtext="
                f"{shape_font_expr}"
                f"textfile='{escape_filter_path(shape_file)}':"
                f"x={bubble_x}+{body_w + 4}:y={bubble_y}-5:"
                f"fontsize={bubble_h + 18}:fontcolor=white@0.97:"
                "box=0:borderw=0:shadowx=0:shadowy=0"
                f"[{next_label}]"
            )
            current_label = next_label
            stage_idx += 1
            next_label = f"v{stage_idx}"
            graph.append(
                f"[{current_label}]drawtext="
                f"{font_expr}"
                f"textfile='{escape_filter_path(bubble_file)}':"
                f"x={bubble_x}+({bubble_w}-text_w)/2:y={bubble_y}+({bubble_h}-text_h)/2:"
                f"fontsize={font_size}:fontcolor=0x111111:line_spacing={line_spacing}:"
                "box=0:borderw=0:shadowx=0:shadowy=0"
                f"[{next_label}]"
            )
            current_label = next_label
            stage_idx += 1

    if duration_s > 2:
        graph.append(
            f"[{current_label}]fade=t=in:st=0:d=0.18,fade=t=out:st={max(0.0, duration_s - 0.18):.2f}:d=0.18,format=yuv420p[vout]"
        )
    else:
        graph.append(f"[{current_label}]format=yuv420p[vout]")

    return ";".join(graph)


def write_srt(episode: dict, output_srt: Path, scenes_override: list[dict] | None = None) -> None:
    output_srt.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    cursor = 0.0
    scenes = scenes_override if scenes_override is not None else episode["scenes"]
    entry_index = 1
    for scene in scenes:
        blocks = scene_caption_blocks(scene)
        if not blocks:
            duration = float(scene["estimated_seconds"])
            start = fmt_srt_time(cursor)
            end = fmt_srt_time(cursor + duration)
            text = sanitize_display_narration(str(scene["narration"]).strip().replace("\n", " "))
            lines.extend([str(entry_index), f"{start} --> {end}", text, ""])
            entry_index += 1
            cursor += duration
            continue

        for block in blocks:
            duration = float(block.get("duration_seconds", 0))
            if duration <= 0:
                continue
            start = fmt_srt_time(cursor)
            end = fmt_srt_time(cursor + duration)
            proper_nouns = set(block.get("proper_nouns", [])) if isinstance(block.get("proper_nouns"), list) else set()
            text = lowercase_after_ellipsis_if_needed(
                str(block.get("dialogue_line") or block.get("text") or "").replace("\n", " "),
                proper_nouns=proper_nouns,
            )
            if text:
                lines.extend([str(entry_index), f"{start} --> {end}", text, ""])
                entry_index += 1
            cursor += duration
    output_srt.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def scene_caption_blocks(scene: dict) -> list[dict]:
    scene_focus_bbox = []
    camera_track = scene.get("camera_track")
    if isinstance(camera_track, dict) and isinstance(camera_track.get("focus_bbox"), list):
        scene_focus_bbox = list(camera_track.get("focus_bbox", []))
    raw_blocks = scene.get("text_phases")
    if not isinstance(raw_blocks, list) or not raw_blocks:
        raw_blocks = scene.get("caption_blocks")
    if isinstance(raw_blocks, list) and raw_blocks:
        output: list[dict] = []
        for idx, block in enumerate(raw_blocks, start=1):
            proper_nouns = list(block.get("proper_nouns", [])) if isinstance(block.get("proper_nouns"), list) else []
            block_text = lowercase_after_ellipsis_if_needed(
                sanitize_display_narration(str(block.get("text", ""))),
                proper_nouns=set(proper_nouns),
            )
            if not block_text:
                continue
            dialogue_line = lowercase_after_ellipsis_if_needed(
                str(block.get("dialogue_line", "")),
                proper_nouns=set(proper_nouns),
            )
            output.append(
                {
                    "block_index": int(block.get("phase_index", block.get("block_index", idx))),
                    "text": block_text,
                    "phase_kind": normalize_ws(str(block.get("phase_kind", "narration"))) or "narration",
                    "paragraph_index": int(block.get("paragraph_index", 0)),
                    "paragraph_block_index": int(block.get("paragraph_block_index", idx - 1)),
                    "paragraph_blocks_total": int(block.get("paragraph_blocks_total", 1)),
                    "duration_seconds": int(block.get("duration_seconds", 1)),
                    "phase_start_s": float(block.get("phase_start_s", 0.0)),
                    "phase_end_s": float(block.get("phase_end_s", 0.0)),
                    "image_path": str(block.get("image_path", scene.get("scene_image_path", scene.get("image_path", "")))),
                    "dialogue_speaker": normalize_ws(str(block.get("dialogue_speaker", ""))),
                    "dialogue_line": dialogue_line,
                    "dialogue_delivery": normalize_dialogue_delivery(str(block.get("dialogue_delivery", "")), dialogue_line),
                    "primary_actor": normalize_ws(str(block.get("primary_actor", ""))),
                    "overlay_bbox": list(block.get("overlay_bbox", [])) if isinstance(block.get("overlay_bbox"), list) else [],
                    "focus_bbox": list(block.get("focus_bbox", [])) if isinstance(block.get("focus_bbox"), list) and block.get("focus_bbox") else list(scene_focus_bbox),
                    "proper_nouns": proper_nouns,
                }
            )
        if output:
            return output

    fallback_text = sanitize_display_narration(str(scene.get("narration", "")).strip())
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
            "phase_start_s": float(sum(fallback_durations[: idx - 1])),
            "phase_end_s": float(sum(fallback_durations[:idx])),
            "image_path": str(scene.get("scene_image_path", fallback_image)),
            "dialogue_speaker": "",
            "dialogue_line": "",
            "dialogue_delivery": "normal",
            "phase_kind": "narration",
            "primary_actor": "",
            "overlay_bbox": [],
            "focus_bbox": list(scene_focus_bbox),
            "proper_nouns": [],
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
    dialogue_delivery: str,
    focus_anchor: str,
    bubble_anchor: str,
    scene_duration_s: int,
    phase_start_s: float,
    phase_end_s: float,
    focus_bbox: list[float] | None,
    overlay_bbox: list[float] | None,
    overlay_assets: dict[str, Path | None],
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
        dialogue_delivery=dialogue_delivery,
        focus_anchor=focus_anchor,
        bubble_anchor=bubble_anchor,
        overlay_assets=overlay_assets,
        scene_duration_s=scene_duration_s,
        phase_start_s=phase_start_s,
        phase_end_s=phase_end_s,
        focus_bbox=focus_bbox,
        overlay_bbox=overlay_bbox,
    )
    af = f"[1:a]apad=pad_dur={duration_s},atrim=0:{duration_s}[aout]"
    filter_complex = f"{vf};{af}"

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        image_path.as_posix(),
        "-ss",
        f"{audio_start_s:.3f}",
        "-t",
        str(duration_s),
        "-i",
        audio_path.as_posix(),
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
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
    parser.add_argument(
        "--overlay-assets-dir",
        default=os.getenv("VIDEO_OVERLAY_ASSETS_DIR", str(DEFAULT_OVERLAY_DIR)),
        help="Directory with narration.svg, dialogue.svg and shout.svg overlays.",
    )
    parser.add_argument("--burn-subtitles", action="store_true", help="Burn subtitles into the final MP4")
    parser.add_argument("--no-burn-subtitles", action="store_true", help="Do not burn subtitles into final MP4")
    args = parser.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ERROR: ffmpeg not found. Install ffmpeg first.")
        return 1

    try:
        font_file = resolve_font_file(args.font_file)
        overlay_assets = resolve_overlay_assets(args.overlay_assets_dir)
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
                    dialogue_delivery=str(block.get("dialogue_delivery", "normal")),
                    focus_anchor="upper_center",
                    bubble_anchor="",
                    scene_duration_s=target_duration,
                    phase_start_s=float(block.get("phase_start_s", audio_cursor)),
                    phase_end_s=float(block.get("phase_end_s", audio_cursor + block_duration)),
                    focus_bbox=list(block.get("focus_bbox", [])) if isinstance(block.get("focus_bbox"), list) else [],
                    overlay_bbox=list(block.get("overlay_bbox", [])) if isinstance(block.get("overlay_bbox"), list) else [],
                    overlay_assets=overlay_assets,
                )
                concat_lines.append(f"file '{segment_path.as_posix()}'")
                audio_cursor += float(block_duration)

        concat_list.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
        compose_concat(ffmpeg, concat_list, merged_video)
        write_srt(episode, output_srt, scenes_override=manifest_scenes)

        burn_subtitles_enabled = bool(args.burn_subtitles and not args.no_burn_subtitles)

        if burn_subtitles_enabled:
            burn_subtitles(ffmpeg, merged_video, output_video, output_srt)
        else:
            shutil.copy2(merged_video, output_video)

        print(f"Composed final video: {output_video}")
        print(f"Subtitles: {output_srt}")
        print(f"Assets manifest: {manifest_path}")
        print(f"Comic font: {font_file or 'system default'}")
        print(f"Overlay assets dir: {args.overlay_assets_dir}")
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: ffmpeg command failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
