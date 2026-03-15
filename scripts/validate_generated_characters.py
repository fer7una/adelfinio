#!/usr/bin/env python3
"""Validate generated character files and timeline files against schemas."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError as exc:
    print("ERROR: missing dependency 'jsonschema'. Install with: python3 -m pip install jsonschema")
    raise SystemExit(1) from exc

ROOT = Path(__file__).resolve().parents[1]
CHAR_SCHEMA = ROOT / "schemas" / "character.schema.json"
TIMELINE_SCHEMA = ROOT / "schemas" / "character_timeline.schema.json"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_dir(schema_path: Path, target_dir: Path, skip_names: set[str]) -> tuple[int, int]:
    schema = load_json(schema_path)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    checked = 0
    failed = 0
    for path in sorted(target_dir.glob("*.json")):
        if path.name in skip_names:
            continue
        checked += 1
        doc = load_json(path)
        errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
        if errors:
            failed += 1
            print(f"FAIL: {path}")
            for err in errors:
                location = ".".join(str(x) for x in err.absolute_path) or "<root>"
                print(f"  - {location}: {err.message}")
        else:
            print(f"OK: {path}")
    return checked, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--characters-dir", default=str(ROOT / "data" / "characters"), help="Characters directory")
    parser.add_argument(
        "--timelines-dir",
        default=str(ROOT / "data" / "characters" / "timelines"),
        help="Character timelines directory",
    )
    args = parser.parse_args()

    characters_dir = Path(args.characters_dir)
    timelines_dir = Path(args.timelines_dir)

    c_checked, c_failed = validate_dir(CHAR_SCHEMA, characters_dir, {"character_example.json"})
    t_checked, t_failed = validate_dir(TIMELINE_SCHEMA, timelines_dir, {"character_timeline_example.json"})

    print(f"\nChecked characters: {c_checked}, failed: {c_failed}")
    print(f"Checked timelines: {t_checked}, failed: {t_failed}")
    return 1 if (c_failed or t_failed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
