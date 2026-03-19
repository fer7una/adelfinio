#!/usr/bin/env python3
"""Layout analysis helpers for scene-level focus and text placement."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

try:
    from pipeline_common import (
        build_openai_client,
        normalize_ws,
        run_structured_generation_with_content,
        write_json,
    )
except ModuleNotFoundError:  # Support package-style imports in local tooling.
    from scripts.pipeline_common import (
        build_openai_client,
        normalize_ws,
        run_structured_generation_with_content,
        write_json,
    )


FRAME_FALLBACK = {"width": 1080, "height": 1920}
DIALOGUE_BOX = {"width": 680, "height": 260}
NARRATION_BOX = {"width": 900, "height": 310}
LAYOUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "focus_target": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "label": {"type": "string"},
                "kind": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "bbox": {
                    "type": "array",
                    "items": {"type": "number", "minimum": 0, "maximum": 1},
                    "minItems": 4,
                    "maxItems": 4,
                },
            },
            "required": ["label", "kind", "confidence", "bbox"],
        },
        "protected_regions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string"},
                    "kind": {"type": "string"},
                    "bbox": {
                        "type": "array",
                        "items": {"type": "number", "minimum": 0, "maximum": 1},
                        "minItems": 4,
                        "maxItems": 4,
                    },
                    "importance": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["label", "kind", "bbox", "importance"],
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["focus_target", "protected_regions", "notes"],
}


def print_mode_warning(mock_mode: bool) -> None:
    if mock_mode:
        print(
            "WARNING: layout_analysis.py is running with --mock. Heuristic fallback will be used and no OpenAI API call will be made.",
            file=os.sys.stderr,
        )
        return
    print(
        "WARNING: layout_analysis.py may call the OpenAI Responses API for visual analysis.\n"
        "To disable OpenAI calls entirely, rerun with --mock.",
        file=os.sys.stderr,
    )


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def clamp_bbox(bbox: list[float]) -> list[float]:
    if len(bbox) != 4:
        return [0.35, 0.24, 0.30, 0.28]
    x, y, w, h = [float(item) for item in bbox]
    w = clamp(w, 0.05, 0.95)
    h = clamp(h, 0.05, 0.95)
    x = clamp(x, 0.0, 1.0 - w)
    y = clamp(y, 0.0, 1.0 - h)
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


def bbox_intersection(a: list[float], b: list[float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    overlap_w = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    overlap_h = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    return overlap_w * overlap_h


def bbox_center(bbox: list[float]) -> tuple[float, float]:
    x, y, w, h = bbox
    return x + (w / 2), y + (h / 2)


def bbox_area(bbox: list[float]) -> float:
    return max(0.0, bbox[2]) * max(0.0, bbox[3])


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return (dx * dx + dy * dy) ** 0.5


def image_data_url(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    mime = mime or "image/png"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def probe_image_size(image_path: Path) -> dict[str, int]:
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        cmd = [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            image_path.as_posix(),
        ]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            raw = normalize_ws(result.stdout)
            if "x" in raw:
                width, height = raw.split("x", 1)
                return {
                    "width": max(1, int(width)),
                    "height": max(1, int(height)),
                }
        except (subprocess.CalledProcessError, ValueError):
            pass
    return dict(FRAME_FALLBACK)


def heuristic_focus_bbox(scene: dict, primary_actor: str) -> list[float]:
    scene_text = " ".join(
        normalize_ws(str(scene.get(field, "")))
        for field in ("visual_focus", "visual_prompt", "scene_objective")
    ).lower()
    if any(token in scene_text for token in ("izquierda", "a la izquierda", "lado izquierdo", "flanco izquierdo")):
        return [0.08, 0.18, 0.36, 0.34]
    if any(token in scene_text for token in ("derecha", "a la derecha", "lado derecho", "flanco derecho")):
        return [0.56, 0.18, 0.36, 0.34]
    if primary_actor:
        return [0.32, 0.18, 0.36, 0.34]
    return [0.24, 0.22, 0.52, 0.32]


def fallback_layout_analysis(scene: dict, phases: list[dict], image_size: dict[str, int]) -> dict[str, Any]:
    primary_actor = ""
    for phase in phases:
        primary_actor = normalize_ws(str(phase.get("primary_actor", "")))
        if primary_actor:
            break
    focus_bbox = heuristic_focus_bbox(scene, primary_actor)
    protected_regions = [
        {
            "label": primary_actor or "foco_principal",
            "kind": "focus",
            "bbox": focus_bbox,
            "importance": 1.0,
        }
    ]
    cast = scene.get("scene_cast")
    if isinstance(cast, list) and len(cast) > 1:
        protected_regions.append(
            {
                "label": "grupo_secundario",
                "kind": "ensemble",
                "bbox": [0.14, 0.44, 0.72, 0.28],
                "importance": 0.55,
            }
        )
    return {
        "focus_target": {
            "label": primary_actor or "foco_principal",
            "kind": "person" if primary_actor else "action",
            "confidence": 0.42,
            "bbox": focus_bbox,
        },
        "protected_regions": protected_regions,
        "notes": "Fallback heuristic layout. No visual model was used.",
        "image_size": image_size,
    }


def build_layout_user_text(scene: dict, phases: list[dict], image_size: dict[str, int]) -> str:
    phase_lines: list[str] = []
    for phase in phases[:8]:
        text = normalize_ws(str(phase.get("dialogue_line") or phase.get("text") or ""))
        phase_lines.append(
            f"- phase {phase.get('phase_index', phase.get('block_index', 0))}: "
            f"kind={phase.get('phase_kind', 'narration')}; speaker={phase.get('dialogue_speaker', '')}; "
            f"primary_actor={phase.get('primary_actor', '')}; text={text}"
        )
    return (
        f"image_size: {image_size['width']}x{image_size['height']}\n"
        f"scene_objective: {normalize_ws(str(scene.get('scene_objective', '')))}\n"
        f"visual_focus: {normalize_ws(str(scene.get('visual_focus', '')))}\n"
        f"visual_prompt: {normalize_ws(str(scene.get('visual_prompt', '')))}\n"
        f"scene_cast: {json.dumps(scene.get('scene_cast', []), ensure_ascii=False)}\n"
        "text_phases:\n"
        + "\n".join(phase_lines)
    )


def analyze_scene_layout(
    *,
    image_path: Path,
    scene: dict,
    phases: list[dict],
    client: Any | None,
    model: str | None,
    reasoning_effort: str = "low",
    use_model: bool = True,
) -> dict[str, Any]:
    image_size = probe_image_size(image_path)
    if not use_model or client is None or not model:
        return fallback_layout_analysis(scene, phases, image_size)

    system_prompt = (
        "Analiza una ilustracion vertical de comic historico para video 9:16. "
        "Debes localizar el foco principal real de la escena y las regiones que no deben taparse con overlays. "
        "Prioriza rostros, manos expresivas, armas activas, edificios clave, estandartes y el gesto central de la accion. "
        "Devuelve bounding boxes normalizadas [x,y,w,h] entre 0 y 1. "
        "No inventes elementos que no aparezcan en la imagen. "
        "El foco principal puede estar arriba, en medio o abajo."
    )
    user_text = build_layout_user_text(scene, phases, image_size)
    try:
        payload = run_structured_generation_with_content(
            client=client,
            model=model,
            schema_name="scene_layout",
            schema=LAYOUT_SCHEMA,
            system_prompt=system_prompt,
            user_content=[
                {"type": "input_text", "text": user_text},
                {"type": "input_image", "image_url": image_data_url(image_path), "detail": "high"},
            ],
            reasoning_effort=reasoning_effort,
        )
    except Exception:
        return fallback_layout_analysis(scene, phases, image_size)

    focus_target = dict(payload.get("focus_target") or {})
    focus_target["bbox"] = clamp_bbox(list(focus_target.get("bbox") or heuristic_focus_bbox(scene, "")))
    protected: list[dict[str, Any]] = []
    for raw in payload.get("protected_regions") or []:
        if not isinstance(raw, dict):
            continue
        protected.append(
            {
                "label": normalize_ws(str(raw.get("label", ""))) or "region",
                "kind": normalize_ws(str(raw.get("kind", ""))) or "protected",
                "bbox": clamp_bbox(list(raw.get("bbox") or focus_target["bbox"])),
                "importance": clamp(float(raw.get("importance", 0.5)), 0.0, 1.0),
            }
        )
    if not protected:
        protected.append(
            {
                "label": normalize_ws(str(focus_target.get("label", ""))) or "focus",
                "kind": normalize_ws(str(focus_target.get("kind", ""))) or "focus",
                "bbox": focus_target["bbox"],
                "importance": clamp(float(focus_target.get("confidence", 0.6)), 0.2, 1.0),
            }
        )
    return {
        "focus_target": {
            "label": normalize_ws(str(focus_target.get("label", ""))) or "focus",
            "kind": normalize_ws(str(focus_target.get("kind", ""))) or "focus",
            "confidence": clamp(float(focus_target.get("confidence", 0.6)), 0.0, 1.0),
            "bbox": focus_target["bbox"],
        },
        "protected_regions": protected,
        "notes": normalize_ws(str(payload.get("notes", ""))) or "Visual model layout analysis.",
        "image_size": image_size,
    }


def candidate_boxes(kind: str, frame_w: int, frame_h: int, *, box_w: int, box_h: int) -> list[list[float]]:
    margin_x = 28
    margin_y = 26
    step_x = max(42, frame_w // 10)
    step_y = max(46, frame_h // 12)
    candidates: list[list[float]] = []
    preferred_y = [margin_y, frame_h // 6, frame_h // 3, frame_h // 2, int(frame_h * 0.66), frame_h - box_h - margin_y]
    if kind == "narration":
        preferred_y = [frame_h - box_h - margin_y, int(frame_h * 0.64), int(frame_h * 0.48), margin_y]
        center_x = max(margin_x, min(frame_w - box_w - margin_x, round((frame_w - box_w) / 2)))
        xs = [center_x]
    else:
        xs = list(range(margin_x, max(margin_x + 1, frame_w - box_w - margin_x + 1), step_x))
    ys = []
    for value in preferred_y:
        y = max(margin_y, min(frame_h - box_h - margin_y, int(value)))
        if y not in ys:
            ys.append(y)
    for y in ys:
        for x in xs:
            x_pos = max(margin_x, min(frame_w - box_w - margin_x, int(x)))
            candidates.append([x_pos / frame_w, y / frame_h, box_w / frame_w, box_h / frame_h])
    return candidates


def choose_overlay_box(
    *,
    kind: str,
    phase_index: int,
    focus_bbox: list[float],
    protected_regions: list[dict[str, Any]],
    used_boxes: list[list[float]],
    frame_w: int,
    frame_h: int,
) -> list[float]:
    box_dims = NARRATION_BOX if kind == "narration" else DIALOGUE_BOX
    box_w = min(box_dims["width"], frame_w - 56)
    box_h = min(box_dims["height"], frame_h - 52)
    focus_center = bbox_center(focus_bbox)
    best_score = None
    best_box = None
    for candidate in candidate_boxes(kind, frame_w, frame_h, box_w=box_w, box_h=box_h):
        overlap = bbox_intersection(candidate, focus_bbox) * 8.0
        for region in protected_regions:
            overlap += bbox_intersection(candidate, list(region["bbox"])) * (1.0 + float(region.get("importance", 0.5)) * 2.5)
        repeat_penalty = 0.0
        for prev in used_boxes[-2:]:
            repeat_penalty += bbox_intersection(candidate, prev) * 1.5
        candidate_center = bbox_center(candidate)
        if kind == "dialogue":
            distance_penalty = distance(candidate_center, focus_center) * 0.35
            vertical_preference = abs(candidate_center[1] - 0.26) * 0.08
        else:
            distance_penalty = distance(candidate_center, focus_center) * 0.10
            vertical_preference = abs(candidate_center[1] - 0.78) * 0.16
        side_variation = 0.02 if phase_index % 2 else 0.0
        score = overlap + repeat_penalty + distance_penalty + vertical_preference + side_variation
        if best_score is None or score < best_score:
            best_score = score
            best_box = candidate
    if best_box is None:
        return [0.08, 0.72, box_w / frame_w, box_h / frame_h]
    return clamp_bbox(best_box)


def build_camera_track(focus_bbox: list[float]) -> dict[str, Any]:
    _, _, w, h = focus_bbox
    scale_factor = max(w, h)
    zoom_end = clamp(0.42 / max(scale_factor, 0.16), 1.14, 1.34)
    return {
        "focus_bbox": clamp_bbox(focus_bbox),
        "zoom_start": 1.03,
        "zoom_end": round(zoom_end, 3),
    }


def assign_scene_layout(scene: dict, phases: list[dict], analysis: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    image_size = dict(analysis.get("image_size") or FRAME_FALLBACK)
    frame_w = max(1, int(image_size.get("width", FRAME_FALLBACK["width"])))
    frame_h = max(1, int(image_size.get("height", FRAME_FALLBACK["height"])))
    focus_bbox = clamp_bbox(list((analysis.get("focus_target") or {}).get("bbox") or heuristic_focus_bbox(scene, "")))
    protected_regions = list(analysis.get("protected_regions") or [])
    camera_track = build_camera_track(focus_bbox)
    cursor = 0.0
    used_boxes: list[list[float]] = []
    out_phases: list[dict[str, Any]] = []
    locked_narration_box: list[float] | None = None
    locked_dialogue_boxes: dict[str, list[float]] = {}
    for index, phase in enumerate(phases, start=1):
        duration = max(1, int(phase.get("duration_seconds", 1)))
        kind = "dialogue" if normalize_ws(str(phase.get("dialogue_line", ""))) else "narration"
        dialogue_speaker = normalize_ws(str(phase.get("dialogue_speaker", "")))
        paragraph_index = int(phase.get("paragraph_index", -1))
        dialogue_key = ""
        if kind == "dialogue":
            if dialogue_speaker:
                dialogue_key = f"{dialogue_speaker.lower()}::{paragraph_index}"
            else:
                dialogue_key = f"dialogue::{paragraph_index}"
        if kind == "narration" and locked_narration_box is not None:
            overlay_bbox = list(locked_narration_box)
        elif kind == "dialogue" and dialogue_key and dialogue_key in locked_dialogue_boxes:
            overlay_bbox = list(locked_dialogue_boxes[dialogue_key])
        else:
            overlay_bbox = choose_overlay_box(
                kind=kind,
                phase_index=index,
                focus_bbox=focus_bbox,
                protected_regions=protected_regions,
                used_boxes=used_boxes,
                frame_w=frame_w,
                frame_h=frame_h,
            )
            if kind == "narration":
                locked_narration_box = list(overlay_bbox)
            elif kind == "dialogue" and dialogue_key:
                locked_dialogue_boxes[dialogue_key] = list(overlay_bbox)
        used_boxes.append(overlay_bbox)
        item = dict(phase)
        item["phase_index"] = int(phase.get("phase_index", phase.get("block_index", index)))
        item["phase_kind"] = kind
        item["phase_start_s"] = round(cursor, 3)
        item["phase_end_s"] = round(cursor + duration, 3)
        item["overlay_bbox"] = overlay_bbox
        item["focus_bbox"] = focus_bbox
        out_phases.append(item)
        cursor += duration
    return (
        {
            "image_size": image_size,
            "focus_target": analysis.get("focus_target"),
            "protected_regions": protected_regions,
            "notes": analysis.get("notes", ""),
            "camera_track": camera_track,
        },
        out_phases,
    )


def analyze_and_assign_layout(
    *,
    image_path: Path,
    scene: dict,
    phases: list[dict],
    client: Any | None,
    model: str | None,
    reasoning_effort: str,
    use_model: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    analysis = analyze_scene_layout(
        image_path=image_path,
        scene=scene,
        phases=phases,
        client=client,
        model=model,
        reasoning_effort=reasoning_effort,
        use_model=use_model,
    )
    return assign_scene_layout(scene, phases, analysis)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Path to the scene image.")
    parser.add_argument("--scene-json", required=True, help="Path to a JSON object with scene metadata.")
    parser.add_argument("--phases-json", required=True, help="Path to a JSON array with text phases.")
    parser.add_argument("--output", required=True, help="Output JSON path.")
    parser.add_argument("--dotenv", default=str(Path(__file__).resolve().parents[1] / ".env"))
    parser.add_argument("--model", default=os.getenv("OPENAI_LAYOUT_MODEL", os.getenv("OPENAI_EPISODE_MODEL", "gpt-5.4")))
    parser.add_argument("--reasoning-effort", default=os.getenv("OPENAI_LAYOUT_REASONING_EFFORT", "low"))
    parser.add_argument("--mock", action="store_true", help="Force heuristic fallback instead of visual model analysis.")
    args = parser.parse_args()

    image_path = Path(args.image)
    scene = json.loads(Path(args.scene_json).read_text(encoding="utf-8"))
    phases = json.loads(Path(args.phases_json).read_text(encoding="utf-8"))
    print_mode_warning(args.mock)
    client = None
    if not args.mock:
        try:
            client = build_openai_client(Path(args.dotenv))
        except RuntimeError:
            client = None
    layout, phases_out = analyze_and_assign_layout(
        image_path=image_path,
        scene=scene,
        phases=phases,
        client=client,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        use_model=bool(client and not args.mock),
    )
    write_json(Path(args.output), {"layout_analysis": layout, "text_phases": phases_out})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
