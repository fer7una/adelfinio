#!/usr/bin/env python3
"""Shared helpers for the source-first narrative pipeline."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

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


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def trim_text(text: str, max_len: int) -> str:
    compact = normalize_ws(text)
    if len(compact) <= max_len:
        return compact
    if max_len <= 3:
        return compact[:max_len]
    available = max_len - 3
    cut = compact[: available + 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    else:
        cut = cut[:available]
    cut = cut.rstrip(".,;: ")
    if not cut:
        cut = compact[:available].rstrip(".,;: ")
    if len(cut) > available:
        cut = cut[:available].rstrip(".,;: ")
    return cut + "..."


def normalize_year_token(value: str | int | None) -> str:
    raw = normalize_ws("" if value is None else str(value))
    if not raw:
        return ""
    if raw.isdigit() and len(raw) == 3:
        return raw.zfill(4)
    return raw


def slugify(text: str, max_len: int = 48) -> str:
    base = normalize_ws(text).lower()
    base = base.translate(SMART_PUNCT_TRANSLATION)
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    if not base:
        return "item"
    return base[:max_len].rstrip("-") or "item"


def source_ref_file(path: Path) -> str:
    abs_path = path.resolve()
    try:
        return str(abs_path.relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def load_dotenv_if_present(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_schema(schema_filename: str) -> dict[str, Any]:
    schema_path = ROOT / "schemas" / schema_filename
    payload = read_json(schema_path)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid schema: {schema_path}")
    return payload


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    if not pointer or pointer == "#":
        return document
    if not pointer.startswith("#/"):
        raise RuntimeError(f"Unsupported JSON pointer: {pointer}")
    current = document
    for part in pointer[2:].split("/"):
        token = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict):
            raise RuntimeError(f"JSON pointer walks through a non-object node: {pointer}")
        current = current[token]
    return current


def inline_external_schema_refs(node: Any, *, base_dir: Path | None = None, cache: dict[str, Any] | None = None) -> Any:
    if base_dir is None:
        base_dir = ROOT / "schemas"
    if cache is None:
        cache = {}

    if isinstance(node, list):
        return [inline_external_schema_refs(item, base_dir=base_dir, cache=cache) for item in node]
    if not isinstance(node, dict):
        return node

    ref = node.get("$ref")
    if isinstance(ref, str) and not ref.startswith("#"):
        file_part, pointer = (ref.split("#", 1) + [""])[:2]
        target_path = (base_dir / file_part).resolve()
        cache_key = str(target_path)
        if cache_key not in cache:
            cache[cache_key] = read_json(target_path)
        target_schema = cache[cache_key]
        resolved = resolve_json_pointer(target_schema, f"#{pointer}" if pointer else "#")
        merged = deepcopy(resolved)
        for key, value in node.items():
            if key == "$ref":
                continue
            merged[key] = value
        return inline_external_schema_refs(merged, base_dir=base_dir, cache=cache)

    return {key: inline_external_schema_refs(value, base_dir=base_dir, cache=cache) for key, value in node.items()}


def _schema_allows_null(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    node_type = node.get("type")
    if node_type == "null":
        return True
    if isinstance(node_type, list) and "null" in node_type:
        return True
    for key in ("anyOf", "oneOf"):
        options = node.get(key)
        if isinstance(options, list) and any(_schema_allows_null(option) for option in options):
            return True
    return False


def _make_nullable_schema(node: Any) -> Any:
    if not isinstance(node, dict) or _schema_allows_null(node):
        return node

    node_type = node.get("type")
    if isinstance(node_type, str):
        out = deepcopy(node)
        out["type"] = [node_type, "null"]
        return out
    if isinstance(node_type, list):
        out = deepcopy(node)
        out["type"] = [item for item in node_type if item != "null"] + ["null"]
        return out
    return {"anyOf": [deepcopy(node), {"type": "null"}]}


def make_openai_strict_schema(node: Any) -> Any:
    if isinstance(node, list):
        return [make_openai_strict_schema(item) for item in node]
    if not isinstance(node, dict):
        return node

    transformed = {key: make_openai_strict_schema(value) for key, value in node.items()}
    if transformed.get("type") != "object" or "properties" not in transformed:
        return transformed

    properties = transformed.get("properties") or {}
    original_required = set(transformed.get("required") or [])
    strict_properties: dict[str, Any] = {}
    for key, value in properties.items():
        strict_properties[key] = value if key in original_required else _make_nullable_schema(value)

    transformed["properties"] = strict_properties
    transformed["required"] = list(strict_properties.keys())
    transformed["additionalProperties"] = False
    return transformed


def build_openai_client(dotenv_path: Path):
    load_dotenv_if_present(dotenv_path)
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing. Set it in env before running chat generation steps.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Missing dependency 'openai'. Install with: python3 -m pip install openai") from exc
    return OpenAI(
        api_key=api_key,
        organization=os.getenv("OPENAI_ORGANIZATION") or None,
        project=os.getenv("OPENAI_PROJECT") or None,
    )


def extract_response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", "")
    if output_text:
        return output_text

    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", "")
            if text:
                return text
    raise RuntimeError("OpenAI response did not contain text output.")


def run_structured_generation(
    *,
    client: Any,
    model: str,
    schema_name: str,
    schema: dict[str, Any],
    system_prompt: str,
    user_prompt: str,
    reasoning_effort: str = "medium",
) -> dict[str, Any]:
    strict_schema = make_openai_strict_schema(schema)
    response = client.responses.create(
        model=model,
        reasoning={"effort": reasoning_effort},
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": user_prompt}],
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": strict_schema,
                "strict": True,
            }
        },
    )
    raw = extract_response_text(response)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Structured response is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Structured response root must be a JSON object.")
    return payload


def run_structured_generation_with_content(
    *,
    client: Any,
    model: str,
    schema_name: str,
    schema: dict[str, Any],
    system_prompt: str,
    user_content: list[dict[str, Any]],
    reasoning_effort: str = "medium",
) -> dict[str, Any]:
    strict_schema = make_openai_strict_schema(schema)
    response = client.responses.create(
        model=model,
        reasoning={"effort": reasoning_effort},
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": user_content,
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": strict_schema,
                "strict": True,
            }
        },
    )
    raw = extract_response_text(response)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Structured response is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Structured response root must be a JSON object.")
    return payload


def default_generation_meta(*, model: str, prompt_version: str, prompt_body: str, input_checksum: str) -> dict[str, Any]:
    return {
        "model": model,
        "prompt_version": prompt_version,
        "prompt_checksum": sha256_text(prompt_body),
        "input_checksum": input_checksum,
        "generated_at": now_iso(),
    }
