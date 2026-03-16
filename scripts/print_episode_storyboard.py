#!/usr/bin/env python3
"""Print a compact storyboard view for an episode JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid JSON payload: {path}")
    return payload


def normalize(text: str) -> str:
    return " ".join((text or "").split()).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", required=True, help="Path to episode JSON")
    args = parser.parse_args()

    episode_path = Path(args.episode)
    episode = load_json(episode_path)
    scenes = episode.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise RuntimeError("Episode has no scenes.")

    print(f"Episode: {episode.get('episode_id')}")
    print(f"Type: {episode.get('episode_type')} | Scenes: {len(scenes)} | Duration: {episode.get('duration_seconds')}s")
    storytelling = episode.get("storytelling")
    if isinstance(storytelling, dict):
        premise = normalize(str(storytelling.get("premise", "")))
        question = normalize(str(storytelling.get("dramatic_question", "")))
        if premise:
            print(f"Premisa: {premise}")
        if question:
            print(f"Pregunta: {question}")
    print("")
    for scene in scenes:
        idx = int(scene.get("scene_index", 0))
        story_role = normalize(str(scene.get("story_role", "")))
        source_beat = normalize(str(scene.get("source_beat", "")))
        visual_focus = normalize(str(scene.get("visual_focus", "")))
        transition_note = normalize(str(scene.get("transition_note", "")))
        narration = normalize(str(scene.get("narration", "")))
        visual_prompt = normalize(str(scene.get("visual_prompt", "")))
        timing = scene.get("timing") if isinstance(scene.get("timing"), dict) else {}
        print(f"[Scene {idx:02d}]")
        if story_role:
            print(f"Rol: {story_role}")
        if source_beat:
            print(f"Beat fuente: {source_beat}")
        if visual_focus:
            print(f"Se muestra: {visual_focus}")
        print(f"Texto: {narration}")
        if timing:
            total = timing.get("target_duration_seconds", scene.get("estimated_seconds"))
            narration_seconds = timing.get("narration_seconds", "?")
            dialogue_seconds = timing.get("dialogue_seconds", "?")
            hold_seconds = timing.get("visual_hold_seconds", "?")
            total_words = timing.get("total_words", "?")
            print(
                f"Tiempo: {total}s "
                f"(narracion {narration_seconds}s, dialogo {dialogue_seconds}s, respiro visual {hold_seconds}s, {total_words} palabras)"
            )
        print(f"Prompt: {visual_prompt}")
        dialogue = scene.get("dialogue")
        if isinstance(dialogue, list) and dialogue:
            for row in dialogue:
                if not isinstance(row, dict):
                    continue
                speaker = normalize(str(row.get("speaker", "")))
                line = normalize(str(row.get("line", "")))
                delivery = normalize(str(row.get("delivery", "")))
                if speaker and line:
                    suffix = f" [{delivery}]" if delivery else ""
                    print(f"Dialogo: {speaker} -> {line}{suffix}")
        else:
            print("Dialogo: sin bocadillos")
        if transition_note:
            print(f"Transicion: {transition_note}")
        print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
