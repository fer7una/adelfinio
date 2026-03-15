#!/usr/bin/env python3
"""Validate project JSON examples against JSON Schemas."""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError as exc:
    print("ERROR: missing dependency 'jsonschema'.")
    print("Install with: python3 -m pip install jsonschema")
    raise SystemExit(1) from exc


ROOT = Path(__file__).resolve().parents[1]

VALIDATION_TARGETS = [
    (
        ROOT / "schemas" / "character.schema.json",
        ROOT / "data" / "characters" / "character_example.json",
    ),
    (
        ROOT / "schemas" / "episode.schema.json",
        ROOT / "data" / "episodes" / "episode_example_main.json",
    ),
    (
        ROOT / "schemas" / "episode.schema.json",
        ROOT / "data" / "episodes" / "episode_example_teaser.json",
    ),
    (
        ROOT / "schemas" / "source_event.schema.json",
        ROOT / "data" / "timeline" / "source_event_example.json",
    ),
    (
        ROOT / "schemas" / "daily_plan.schema.json",
        ROOT / "data" / "daily_plan" / "daily_plan_example.json",
    ),
    (
        ROOT / "schemas" / "character_timeline.schema.json",
        ROOT / "data" / "characters" / "timelines" / "character_timeline_example.json",
    ),
]


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    failures: list[str] = []

    for schema_path, document_path in VALIDATION_TARGETS:
        if not schema_path.exists():
            failures.append(f"Missing schema: {schema_path}")
            continue
        if not document_path.exists():
            failures.append(f"Missing document: {document_path}")
            continue

        schema = load_json(schema_path)
        document = load_json(document_path)

        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = sorted(validator.iter_errors(document), key=lambda e: list(e.path))
        if errors:
            failures.append(f"{document_path} failed validation:")
            for err in errors:
                location = ".".join([str(x) for x in err.absolute_path]) or "<root>"
                failures.append(f"  - {location}: {err.message}")
        else:
            print(f"OK: {document_path.relative_to(ROOT)}")

    if failures:
        print("\nValidation failed:")
        for line in failures:
            print(line)
        return 1

    print("\nAll JSON examples validated successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
