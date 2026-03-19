#!/usr/bin/env python3
"""Shared helper functions for overlay layout and FFmpeg render composition."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

try:
    from video_text_layout import TEXT_FIT_CHAR_WIDTH_FACTOR, mode_char_width_factor, wrap_text_unbounded
except ModuleNotFoundError:
    from scripts.video_text_layout import TEXT_FIT_CHAR_WIDTH_FACTOR, mode_char_width_factor, wrap_text_unbounded

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OVERLAY_DIR = ROOT / "assets" / "video_overlays"


def wrap_text(text: str, width: int = 22, max_lines: int = 3) -> str:
    words = re.sub(r"\s+", " ", text or "").strip().split()
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
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return "", min_font_size, max(4, round(min_font_size * 0.16))

    for font_size in range(max_font_size, min_font_size - 1, -1):
        line_spacing = max(4, round(font_size * 0.16))
        base_chars = max(8, int(box_w / max(1.0, font_size * char_width_factor)))
        for extra_chars in (0, 1, 2, 3, 4, 5):
            max_chars = base_chars + extra_chars
            lines = wrap_text_unbounded(clean, max_chars)
            if not lines or len(lines) > max_lines:
                continue
            wrapped = "\n".join(lines)
            est_width = max(len(line) for line in lines) * font_size * char_width_factor
            est_height = (len(lines) * font_size) + ((len(lines) - 1) * line_spacing)
            if est_width <= (box_w * 1.04) and est_height <= (box_h * 1.03):
                return wrapped, font_size, line_spacing

    fallback_spacing = max(4, round(min_font_size * 0.16))
    fallback_chars = max(8, int(box_w / max(1.0, min_font_size * char_width_factor)))
    return wrap_text(clean, width=fallback_chars, max_lines=max_lines), min_font_size, fallback_spacing


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
    pad_x = max(56, round(overlay_w * (0.18 if shout else 0.15)))
    pad_y = max(44, round(overlay_h * (0.20 if shout else 0.17)))
    text_x, text_y, text_box_w, text_box_h = centered_text_position(
        str(overlay_x),
        str(overlay_y),
        overlay_w=overlay_w,
        overlay_h=overlay_h,
        pad_x=pad_x,
        pad_y=pad_y,
    )
    return str(overlay_x), str(overlay_y), text_x, text_y, text_box_w, text_box_h


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
