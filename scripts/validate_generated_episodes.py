#!/usr/bin/env python3
"""Validate generated episodes against episode.schema.json."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from schema_validation import build_validator, load_json
except ImportError as exc:
    print("ERROR: missing dependency 'jsonschema'. Install with: python3 -m pip install jsonschema")
    raise SystemExit(1) from exc

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "episode.schema.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        dest="episodes_dir",
        required=True,
        help="Directory containing generated episode JSON files",
    )
    args = parser.parse_args()

    episodes_dir = Path(args.episodes_dir)
    if not episodes_dir.exists():
        print(f"ERROR: directory not found: {episodes_dir}")
        return 1

    validator = build_validator(SCHEMA_PATH)

    files = sorted(episodes_dir.glob("*.json"))
    if not files:
        print(f"ERROR: no JSON files found in {episodes_dir}")
        return 1

    failures = 0
    for path in files:
        doc = load_json(path)
        errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
        if errors:
            failures += 1
            print(f"FAIL: {path}")
            for err in errors:
                location = ".".join(str(x) for x in err.absolute_path) or "<root>"
                print(f"  - {location}: {err.message}")
        else:
            print(f"OK: {path}")

    if failures:
        print(f"\nValidation failed: {failures} files with errors.")
        return 1

    print("\nAll generated episodes are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
