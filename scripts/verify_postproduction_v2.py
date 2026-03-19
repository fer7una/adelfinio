#!/usr/bin/env python3
"""Verify composed V2 scene overlays with OCR before final assembly."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

try:
    from video_pipeline_v2 import ROOT, load_json, scene_paths
except ModuleNotFoundError:
    from scripts.video_pipeline_v2 import ROOT, load_json, scene_paths


DEFAULT_RENDER_PLAN_DIR = ROOT / "artifacts" / "render_plan"
DEFAULT_REPORT_DIR = ROOT / "artifacts" / "render_plan"
DEFAULT_DEBUG_DIR = ROOT / ".tmp" / "postproduction_checks"
OCR_LANG = "spa"
OCR_PSM = "6"
TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class VerificationIssue:
    scene_index: int
    event_id: str
    message: str
    expected: str
    observed: str
    ratio: float
    crop_path: str


def ensure_program(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Required program not found: {name}")
    return path


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_tokens(text: str) -> list[str]:
    folded = strip_accents(str(text or "").lower())
    folded = folded.replace("...", " ")
    folded = re.sub(r"[^a-z0-9]+", " ", folded)
    return [token for token in folded.split() if token]


def normalized_expected_text(text: str) -> str:
    return " ".join(normalize_tokens(text))


def parse_tsv_words(tsv_text: str) -> tuple[list[dict[str, Any]], float]:
    words: list[dict[str, Any]] = []
    confidences: list[float] = []
    for line in tsv_text.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 12 or parts[0] != "5":
            continue
        word = str(parts[11]).strip()
        if not word:
            continue
        try:
            conf = float(parts[10])
            left = int(float(parts[6]))
            top = int(float(parts[7]))
            width = int(float(parts[8]))
            height = int(float(parts[9]))
        except ValueError:
            continue
        if conf >= 0:
            confidences.append(conf / 100.0)
        words.append(
            {
                "text": word,
                "conf": round(max(0.0, conf) / 100.0, 4),
                "left": left,
                "top": top,
                "width": width,
                "height": height,
            }
        )
    mean_conf = round(sum(confidences) / len(confidences), 4) if confidences else 0.0
    return words, mean_conf


def extract_crop(ffmpeg: str, video_path: Path, timestamp_s: float, crop_box: tuple[int, int, int, int], output_path: Path) -> None:
    crop_x, crop_y, crop_w, crop_h = crop_box
    output_path.parent.mkdir(parents=True, exist_ok=True)
    vf = f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale=iw*2:ih*2:flags=lanczos"
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        f"{max(0.0, timestamp_s):.3f}",
        "-i",
        video_path.as_posix(),
        "-frames:v",
        "1",
        "-vf",
        vf,
        output_path.as_posix(),
    ]
    proc = run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg crop failed for {video_path}: {(proc.stderr or '').strip()}")


def ocr_crop(tesseract: str, image_path: Path) -> tuple[list[dict[str, Any]], str, float]:
    txt_proc = run([tesseract, image_path.as_posix(), "stdout", "-l", OCR_LANG, "--psm", OCR_PSM])
    if txt_proc.returncode != 0:
        raise RuntimeError(f"Tesseract OCR failed for {image_path}: {(txt_proc.stderr or '').strip()}")
    tsv_proc = run([tesseract, image_path.as_posix(), "stdout", "-l", OCR_LANG, "--psm", OCR_PSM, "tsv"])
    if tsv_proc.returncode != 0:
        raise RuntimeError(f"Tesseract TSV OCR failed for {image_path}: {(tsv_proc.stderr or '').strip()}")
    words, mean_conf = parse_tsv_words(tsv_proc.stdout)
    return words, txt_proc.stdout, mean_conf


def word_boxes_touch_edges(words: list[dict[str, Any]], crop_w: int, crop_h: int, margin_px: int = 2) -> bool:
    for word in words:
        left = int(word["left"])
        top = int(word["top"])
        right = left + int(word["width"])
        bottom = top + int(word["height"])
        if left <= margin_px or top <= margin_px or right >= crop_w - margin_px or bottom >= crop_h - margin_px:
            return True
    return False


def compare_text(expected: str, observed: str) -> float:
    expected_tokens = normalize_tokens(expected)
    observed_tokens = normalize_tokens(observed)
    if not expected_tokens and not observed_tokens:
        return 1.0
    if not expected_tokens or not observed_tokens:
        return 0.0
    return SequenceMatcher(None, expected_tokens, observed_tokens).ratio()


def verify_event(
    *,
    ffmpeg: str,
    tesseract: str,
    video_path: Path,
    event: dict[str, Any],
    canvas_w: int,
    canvas_h: int,
    debug_dir: Path,
) -> VerificationIssue | None:
    event_id = str(event["event_id"])
    expected = normalized_expected_text(str(event.get("text", "")))
    if not expected:
        return None

    box = list(event.get("region", {}).get("box_norm", []))
    layout = dict(event.get("layout") or {})
    if len(box) != 4:
        return VerificationIssue(
            scene_index=int(event.get("scene_index", 0)),
            event_id=event_id,
            message="Missing overlay box",
            expected=expected,
            observed="",
            ratio=0.0,
            crop_path="",
        )

    box_x = max(0, round(float(box[0]) * canvas_w))
    box_y = max(0, round(float(box[1]) * canvas_h))
    box_w = max(1, round(float(box[2]) * canvas_w))
    box_h = max(1, round(float(box[3]) * canvas_h))
    pad_x = int(layout.get("padding_x_px", 0))
    pad_y = int(layout.get("padding_y_px", 0))
    crop_x = min(max(0, box_x + pad_x), canvas_w - 1)
    crop_y = min(max(0, box_y + pad_y), canvas_h - 1)
    crop_w = max(1, min(canvas_w - crop_x, box_w - (2 * pad_x)))
    crop_h = max(1, min(canvas_h - crop_y, box_h - (2 * pad_y)))

    if crop_w <= 2 or crop_h <= 2:
        return VerificationIssue(
            scene_index=int(event.get("scene_index", 0)),
            event_id=event_id,
            message="Invalid crop region",
            expected=expected,
            observed="",
            ratio=0.0,
            crop_path="",
        )

    start_s = float(event.get("start_s", 0.0))
    end_s = float(event.get("end_s", start_s))
    sample_s = start_s + max(0.02, (end_s - start_s) / 2)
    scene_index = int(event.get("scene_index", 0))
    scene_dir = debug_dir / f"scene_{scene_index:02d}"
    scene_dir.mkdir(parents=True, exist_ok=True)
    crop_path = scene_dir / f"{event_id}.png"
    extract_crop(ffmpeg, video_path, sample_s, (crop_x, crop_y, crop_w, crop_h), crop_path)
    words, observed_raw, mean_conf = ocr_crop(tesseract, crop_path)
    observed = normalized_expected_text(observed_raw)
    ratio = compare_text(expected, observed_raw)
    edge_touch = word_boxes_touch_edges(words, crop_w * 2, crop_h * 2, margin_px=2)
    expected_tokens = normalize_tokens(expected)
    observed_tokens = normalize_tokens(observed_raw)

    if not expected_tokens:
        return None

    if not observed_tokens:
        return VerificationIssue(
            scene_index=scene_index,
            event_id=event_id,
            message="OCR produced no readable tokens",
            expected=expected,
            observed=observed,
            ratio=0.0,
            crop_path=crop_path.as_posix(),
        )

    min_ratio = 0.36 if len(expected_tokens) <= 4 else 0.28
    if ratio < min_ratio:
        return VerificationIssue(
            scene_index=scene_index,
            event_id=event_id,
            message=f"Text mismatch below threshold ({ratio:.3f} < {min_ratio:.2f})",
            expected=expected,
            observed=observed,
            ratio=ratio,
            crop_path=crop_path.as_posix(),
        )

    if edge_touch and ratio < 0.35:
        return VerificationIssue(
            scene_index=scene_index,
            event_id=event_id,
            message=f"OCR words touch crop edge (mean_conf={mean_conf:.3f})",
            expected=expected,
            observed=observed,
            ratio=ratio,
            crop_path=crop_path.as_posix(),
        )

    return None


def verify_episode(episode_path: Path, render_plan_dir: Path = DEFAULT_RENDER_PLAN_DIR, debug_dir: Path = DEFAULT_DEBUG_DIR) -> list[VerificationIssue]:
    ffmpeg = ensure_program("ffmpeg")
    tesseract = ensure_program("tesseract")
    episode = load_json(episode_path)
    episode_id = str(episode["episode_id"])
    render_dir = render_plan_dir / episode_id
    issues: list[VerificationIssue] = []
    for overlay_path in sorted(render_dir.glob("scene_*.overlay_timeline.json")):
        overlay = load_json(overlay_path)
        scene_index = int(overlay["scene_index"])
        video_path = scene_paths(episode_id, scene_index).composited_video
        if not video_path.exists():
            issues.append(
                VerificationIssue(
                    scene_index=scene_index,
                    event_id="",
                    message=f"Missing composited video: {video_path}",
                    expected="",
                    observed="",
                    ratio=0.0,
                    crop_path="",
                )
            )
            continue
        canvas = dict(overlay.get("canvas") or {})
        canvas_w = int(canvas.get("width", 1080))
        canvas_h = int(canvas.get("height", 1920))
        for event in overlay.get("events", []) or []:
            if not isinstance(event, dict):
                continue
            issue = verify_event(
                ffmpeg=ffmpeg,
                tesseract=tesseract,
                video_path=video_path,
                event={**event, "scene_index": scene_index},
                canvas_w=canvas_w,
                canvas_h=canvas_h,
                debug_dir=debug_dir / episode_id,
            )
            if issue is not None:
                issues.append(issue)
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", required=True, help="Path to the episode JSON")
    parser.add_argument("--render-plan-dir", default=str(DEFAULT_RENDER_PLAN_DIR), help="Render plan root")
    parser.add_argument("--debug-dir", default=str(DEFAULT_DEBUG_DIR), help="Directory for OCR crops")
    parser.add_argument("--report-path", default=None, help="Optional JSON report path")
    args = parser.parse_args()

    episode_path = Path(args.episode)
    render_plan_dir = Path(args.render_plan_dir)
    debug_dir = Path(args.debug_dir)
    issues = verify_episode(episode_path, render_plan_dir=render_plan_dir, debug_dir=debug_dir)

    report = {
        "episode_id": load_json(episode_path)["episode_id"],
        "ok": not issues,
        "issue_count": len(issues),
        "issues": [issue.__dict__ for issue in issues],
    }
    report_path = Path(args.report_path) if args.report_path else (render_plan_dir / report["episode_id"] / "postproduction_verification.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if issues:
        for issue in issues:
            print(
                f"FAIL scene={issue.scene_index:02d} event={issue.event_id} ratio={issue.ratio:.3f} "
                f"{issue.message} crop={issue.crop_path}"
            )
        print(f"Wrote report: {report_path}")
        return 1

    print(f"OK: verified {report['episode_id']} with no OCR/layout issues")
    print(f"Wrote report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
