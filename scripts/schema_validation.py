#!/usr/bin/env python3
"""Helpers for JSON Schema validation with local schema references."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, RefResolver

ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_validator(schema_path: Path) -> Draft202012Validator:
    schema = load_json(schema_path)
    store: dict[str, dict] = {}
    for candidate in (ROOT / "schemas").glob("*.json"):
        candidate_schema = load_json(candidate)
        schema_id = candidate_schema.get("$id")
        if isinstance(schema_id, str):
            store[schema_id] = candidate_schema
            store[f"{candidate.name}"] = candidate_schema
    resolver = RefResolver.from_schema(schema, store=store)
    return Draft202012Validator(schema, resolver=resolver, format_checker=FormatChecker())
