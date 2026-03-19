#!/usr/bin/env python3
"""Shared text box sizing and fitting helpers for video overlays."""

from __future__ import annotations

import re

DEFAULT_FRAME_WIDTH = 1080
DEFAULT_FRAME_HEIGHT = 1920
DEFAULT_DIALOGUE_OVERLAY_W = 680
DEFAULT_DIALOGUE_OVERLAY_H = 260
DEFAULT_NARRATION_OVERLAY_W = 900
DEFAULT_NARRATION_OVERLAY_H = 310
TEXT_FIT_CHAR_WIDTH_FACTOR = 0.56
PREFERRED_DIALOGUE_MIN_FONT_SIZE = 26
PREFERRED_SHOUT_MIN_FONT_SIZE = 28


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


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


def overlay_text_config(
    mode: str,
    *,
    delivery: str = "normal",
    overlay_bbox: list[float] | None = None,
    frame_w: int = DEFAULT_FRAME_WIDTH,
    frame_h: int = DEFAULT_FRAME_HEIGHT,
) -> dict:
    if mode == "dialogue":
        shout = delivery == "shout"
        _, _, overlay_w, overlay_h = bbox_to_pixels(
            overlay_bbox,
            frame_w=frame_w,
            frame_h=frame_h,
            fallback_w=DEFAULT_DIALOGUE_OVERLAY_W,
            fallback_h=DEFAULT_DIALOGUE_OVERLAY_H,
        )
        pad_x = max(92, round(overlay_w * (0.24 if shout else 0.22)))
        pad_y = max(74, round(overlay_h * (0.27 if shout else 0.24)))
        return {
            "box_w": max(80, overlay_w - (2 * pad_x)),
            "box_h": max(40, overlay_h - (2 * pad_y)),
            "max_font_size": 36 if shout else 38,
            "min_font_size": 22,
            "preferred_min_font_size": PREFERRED_SHOUT_MIN_FONT_SIZE if shout else PREFERRED_DIALOGUE_MIN_FONT_SIZE,
            "max_lines": 3,
            "char_width_factor": TEXT_FIT_CHAR_WIDTH_FACTOR,
        }

    _, _, overlay_w, overlay_h = bbox_to_pixels(
        overlay_bbox,
        frame_w=frame_w,
        frame_h=frame_h,
        fallback_w=DEFAULT_NARRATION_OVERLAY_W,
        fallback_h=DEFAULT_NARRATION_OVERLAY_H,
    )
    left_pad = max(78, round(overlay_w * 0.14))
    right_pad = max(78, round(overlay_w * 0.14))
    top_pad = max(26, round(overlay_h * 0.12))
    bottom_pad = max(74, round(overlay_h * 0.26))
    return {
        "box_w": max(120, overlay_w - left_pad - right_pad),
        "box_h": max(60, overlay_h - top_pad - bottom_pad),
        "max_font_size": 42,
        "min_font_size": 26,
        "preferred_min_font_size": 30,
        "max_lines": 3,
        "char_width_factor": TEXT_FIT_CHAR_WIDTH_FACTOR,
    }


def wrap_text_unbounded(text: str, width: int) -> list[str]:
    words = normalize_ws(text).split()
    if not words:
        return []
    out: list[str] = []
    line: list[str] = []
    current = 0
    for word in words:
        extra = len(word) + (1 if line else 0)
        if line and current + extra > width:
            out.append(" ".join(line))
            line = [word]
            current = len(word)
        else:
            line.append(word)
            current += extra
    if line:
        out.append(" ".join(line))
    return out


def wrap_text(text: str, width: int = 22, max_lines: int = 3) -> str:
    lines = wrap_text_unbounded(text, width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        if not lines[-1].endswith("..."):
            lines[-1] = lines[-1].rstrip(" ,;:") + "..."
    return "\n".join(lines)


def fit_wrapped_text(
    text: str,
    box_w: int,
    box_h: int,
    *,
    max_font_size: int,
    min_font_size: int,
    preferred_min_font_size: int | None = None,
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


def text_fits_overlay(
    text: str,
    *,
    box_w: int,
    box_h: int,
    max_font_size: int,
    min_font_size: int,
    preferred_min_font_size: int | None = None,
    max_lines: int,
    char_width_factor: float = TEXT_FIT_CHAR_WIDTH_FACTOR,
) -> bool:
    clean = normalize_ws(text)
    if not clean:
        return True
    for font_size in range(max_font_size, min_font_size - 1, -1):
        line_spacing = max(4, round(font_size * 0.16))
        max_chars = max(8, int(box_w / max(1.0, font_size * char_width_factor)))
        lines = wrap_text_unbounded(clean, max_chars)
        if not lines or len(lines) > max_lines:
            continue
        est_width = max(len(line) for line in lines) * font_size * char_width_factor
        est_height = (len(lines) * font_size) + ((len(lines) - 1) * line_spacing)
        if est_width <= box_w and est_height <= box_h:
            return True
    return False
