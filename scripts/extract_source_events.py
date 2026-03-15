#!/usr/bin/env python3
"""Extract normalized source events from chronicles files.

Supports:
- .txt / .md (parsed into chronological events)
- .json (already normalized source events)
- .pdf (via mutool text layer; if scanned, fallback to sidecar text)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "timeline" / "source_events.json"
DEFAULT_SOURCE = ROOT / "docs" / "chronicles" / "01-La gran aventura del reino de Asturias.pdf"
DEFAULT_SIDECAR_DIR = ROOT / "docs" / "chronicles" / "sidecar_text"

YEAR_RE = re.compile(r"\b(7\d{2}|8\d{2}|9\d{2}|1[0-4]\d{2})\b")
PAGE_TAG_RE = re.compile(r"^\[PAGE\s+\d+\]$", re.IGNORECASE)
PAGE_NOISE_RE = re.compile(r".*\bPágina\s+\d+\b.*", re.IGNORECASE)
DOT_LEADER_RE = re.compile(r"\.{4,}")
LEGAL_NOISE_RE = re.compile(
    r"(isbn|depósito legal|fotocomposición|reprográfic|impresión|encuadernación|primera edición)",
    re.IGNORECASE,
)

LOCATION_HINTS = [
    "asturias",
    "covadonga",
    "cangas",
    "oviedo",
    "galicia",
    "leon",
    "toledo",
    "cordoba",
]

ACTOR_HINTS = {
    "pelayo": "pelayo",
    "don pelayo": "pelayo",
    "alfonso i": "alfonso_i",
    "alfonso ii": "alfonso_ii",
    "favila": "favila",
    "ermesinda": "ermesinda",
    "munuza": "munuza",
    "witiza": "witiza",
    "abd al-rahman": "abd_al_rahman_i",
}

HISTORICAL_HINTS = [
    "reino",
    "asturias",
    "covadonga",
    "rey",
    "condado",
    "cronica",
    "batalla",
    "alianza",
    "musulman",
    "visigodo",
]

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
    }
)
ALLOWED_SHORT_TOKENS = {
    "a",
    "al",
    "de",
    "del",
    "el",
    "en",
    "es",
    "la",
    "le",
    "lo",
    "los",
    "no",
    "ni",
    "o",
    "se",
    "si",
    "su",
    "te",
    "tu",
    "u",
    "un",
    "y",
    "ya",
}
ROMAN_NUMERAL_RE = re.compile(r"^[ivxlcdm]+$", re.IGNORECASE)
REPEATED_CHAR_TOKEN_RE = re.compile(r"^([a-zA-ZáéíóúüñÁÉÍÓÚÜÑ])\1{1,4}$")
UNSUPPORTED_CHAR_RE = re.compile(r"[^0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ.,;:!?()\"'\\-_/\\s]")


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def source_ref_file(path: Path) -> str:
    abs_path = path.resolve()
    try:
        return str(abs_path.relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)


def read_pdf_with_mutool(path: Path) -> str:
    cmd = ["mutool", "draw", "-F", "txt", "-o", "-", str(path)]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if "unknown encryption handler" in stderr or "cannot draw" in stderr:
            raise RuntimeError(
                "PDF extraction failed due to DRM/encryption. "
                "Use a legal text export (.txt/.md) and retry in strict mode."
            )
        raise RuntimeError(f"PDF extraction failed for {path}: {stderr}")
    return proc.stdout


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean_title(paragraph: str) -> str:
    sentence = paragraph.split(".")[0].strip()
    words = sentence.split()
    title = " ".join(words[:14]).strip()
    if len(title) < 6:
        title = (paragraph[:80] or "Evento historico").strip()
    return title[:240]


def detect_location(paragraph: str) -> str:
    low = paragraph.lower()
    for hint in LOCATION_HINTS:
        if hint in low:
            return hint.capitalize()
    return "Asturias"


def detect_actors(paragraph: str) -> list[str]:
    low = paragraph.lower()
    actors: list[str] = []
    for key, actor_id in ACTOR_HINTS.items():
        if key in low and actor_id not in actors:
            actors.append(actor_id)
    return actors


def infer_base_year(text: str) -> int:
    years = [int(y) for y in YEAR_RE.findall(text)]
    if years:
        return min(years)
    return 718


def infer_year_for_index(base_year: int, index: int) -> str:
    year = base_year + max(index - 1, 0)
    return f"{year:04d}"


def sanitize_event_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).translate(SMART_PUNCT_TRANSLATION)
    normalized = re.sub(
        r"\b([A-ZÁÉÍÓÚÜÑ])([a-záéíóúüñ])\b\s+([a-záéíóúüñ]{2,})",
        lambda m: (m.group(1) + m.group(3)) if m.group(2) == m.group(1).lower() else m.group(0),
        normalized,
    )
    normalized = UNSUPPORTED_CHAR_RE.sub(" ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return ""

    cleaned_tokens: list[str] = []
    for raw_token in normalized.split():
        token = raw_token.strip(".,;:!?()\"'")
        lower = token.lower()
        if not token:
            continue
        if REPEATED_CHAR_TOKEN_RE.fullmatch(token) and not ROMAN_NUMERAL_RE.fullmatch(token):
            continue
        if len(lower) <= 2 and lower not in ALLOWED_SHORT_TOKENS and not ROMAN_NUMERAL_RE.fullmatch(token):
            continue
        cleaned_tokens.append(raw_token)

    cleaned = " ".join(cleaned_tokens)
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"([¿¡])\s+", r"\1", cleaned)
    return cleaned.strip()


def normalize_ocr_text(text: str) -> str:
    lines = text.replace("\r", "\n").splitlines()
    cleaned: list[str] = []

    for line in lines:
        raw = line.strip()
        if not raw:
            cleaned.append("")
            continue
        raw = re.sub(r"\bLA GRAN AVENTURA DEL REINO DE ASTURIAS\b", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"^\d+\s+", "", raw).strip()
        if not raw:
            continue
        if PAGE_TAG_RE.match(raw):
            continue
        if PAGE_NOISE_RE.match(raw):
            continue
        if DOT_LEADER_RE.search(raw):
            continue
        if re.match(r"^\|?\s*[—-]?\d{1,4}[—-]?\s*\|?$", raw):
            continue
        if re.match(r"^[A-Z0-9\s]{1,8}$", raw):
            continue
        cleaned.append(raw)

    # Merge hyphenated line wraps and reconstruct paragraphs.
    paragraphs: list[str] = []
    current = ""
    for line in cleaned:
        if not line:
            if current:
                paragraphs.append(current.strip())
                current = ""
            continue

        if not current:
            current = line
            continue

        if current.endswith("-"):
            current = current[:-1] + line
        else:
            current = current + " " + line

    if current:
        paragraphs.append(current.strip())

    sanitized_paragraphs = [sanitize_event_text(p) for p in paragraphs]
    sanitized_paragraphs = [p for p in sanitized_paragraphs if p]
    return "\n\n".join(sanitized_paragraphs)


def paragraph_to_event(
    paragraph: str,
    source_ref_file: str,
    section: str,
    index: int,
    used_ids: set[str],
    base_year: int,
    allow_inferred_dates: bool,
) -> dict:
    year_match = YEAR_RE.search(paragraph)
    if year_match:
        year = year_match.group(1)
    elif allow_inferred_dates:
        year = infer_year_for_index(base_year, index)
    else:
        raise RuntimeError("paragraph has no detectable year and inferred dates are disabled")

    suffix = 1
    event_id = f"evt-{year}{suffix:02d}"
    while event_id in used_ids:
        suffix += 1
        event_id = f"evt-{year}{suffix:02d}"

    checksum = sha256_text(paragraph)
    actors = detect_actors(paragraph)
    location = detect_location(paragraph)

    confidence = 0.58
    if year_match:
        confidence += 0.12
    if actors:
        confidence += 0.10
    if len(paragraph.split()) > 25:
        confidence += 0.08
    confidence = min(confidence, 0.92)

    return {
        "event_id": event_id,
        "chronology_index": index,
        "title": clean_title(paragraph),
        "date_start": year,
        "date_end": None,
        "location": location,
        "actors": actors,
        "summary": paragraph[:1500],
        "source_ref": {
            "file": source_ref_file,
            "section": section,
            "checksum": checksum,
        },
        "historical_confidence": round(confidence, 2),
    }


def candidate_paragraphs(text: str, min_paragraph_length: int) -> list[str]:
    normalized = normalize_ocr_text(text)
    paragraphs = [sanitize_event_text(p.strip()) for p in re.split(r"\n\s*\n", normalized) if p.strip()]
    paragraphs = [p for p in paragraphs if p]
    paragraphs = [p for p in paragraphs if not LEGAL_NOISE_RE.search(p)]

    with_year = [p for p in paragraphs if YEAR_RE.search(p)]
    if with_year:
        return with_year

    fallback: list[str] = []
    for p in paragraphs:
        low = p.lower()
        if len(p) < min_paragraph_length:
            continue
        if any(h in low for h in HISTORICAL_HINTS):
            fallback.append(p)
    return fallback


def is_usable_paragraph(paragraph: str) -> bool:
    low = paragraph.lower().strip()
    if low.startswith("capítulo") or low.startswith("indice"):
        return False
    if "la gran aventura del reino de asturias" in low and len(paragraph.split()) < 30:
        return False
    if len(paragraph.split()) < 20:
        return False
    return True


def parse_text_to_events(
    text: str,
    source_ref_file: str,
    start_index: int,
    used_ids: set[str],
    allow_inferred_dates: bool,
    min_paragraph_length: int,
) -> list[dict]:
    candidates = candidate_paragraphs(text, min_paragraph_length)
    candidates = [p for p in candidates if is_usable_paragraph(p)]
    if not candidates:
        raise RuntimeError(
            "No candidate chronological paragraphs found. "
            "Provide a cleaner OCR text file or lower --min-paragraph-length."
        )

    base_year = infer_base_year(text)
    events = []
    current_index = start_index
    for p_idx, paragraph in enumerate(candidates, start=1):
        event = paragraph_to_event(
            paragraph,
            source_ref_file,
            f"parrafo_{p_idx}",
            current_index,
            used_ids,
            base_year,
            allow_inferred_dates,
        )
        used_ids.add(event["event_id"])
        events.append(event)
        current_index += 1
    return events


def normalize_json_source(path: Path, start_index: int, used_ids: set[str]) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError(f"JSON source must be a list of events: {path}")
    events = []
    current_index = start_index
    for item in payload:
        if not isinstance(item, dict):
            continue
        required = {"event_id", "title", "date_start", "location", "summary", "source_ref", "historical_confidence"}
        if not required.issubset(item.keys()):
            continue
        ev = dict(item)
        ev["chronology_index"] = current_index
        if ev["event_id"] in used_ids:
            raise RuntimeError(f"Duplicated event_id detected: {ev['event_id']}")
        used_ids.add(ev["event_id"])
        events.append(ev)
        current_index += 1
    if not events:
        raise RuntimeError(f"No valid events found in JSON source: {path}")
    return events


def find_pdf_sidecar(pdf_path: Path, sidecar_dir: Path) -> Path | None:
    stem = pdf_path.stem
    candidates = [
        sidecar_dir / f"{stem}.txt",
        sidecar_dir / f"{stem}.md",
        pdf_path.with_suffix(".txt"),
        pdf_path.with_suffix(".md"),
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def extract_events_from_source(
    path: Path,
    start_index: int,
    used_ids: set[str],
    sidecar_dir: Path,
    allow_inferred_dates: bool,
    min_paragraph_length: int,
) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return parse_text_to_events(
            read_text_file(path),
            source_ref_file(path),
            start_index,
            used_ids,
            allow_inferred_dates,
            min_paragraph_length,
        )
    if suffix == ".json":
        return normalize_json_source(path, start_index, used_ids)
    if suffix == ".pdf":
        raw_text = read_pdf_with_mutool(path)
        if len(raw_text.strip()) > 200:
            return parse_text_to_events(
                raw_text,
                source_ref_file(path),
                start_index,
                used_ids,
                allow_inferred_dates,
                min_paragraph_length,
            )

        sidecar = find_pdf_sidecar(path, sidecar_dir)
        if not sidecar:
            raise RuntimeError(
                "PDF has no usable text layer and no sidecar text was found. "
                f"Create OCR text at: {sidecar_dir / (path.stem + '.txt')}"
            )

        return parse_text_to_events(
            read_text_file(sidecar),
            source_ref_file(path),
            start_index,
            used_ids,
            allow_inferred_dates,
            min_paragraph_length,
        )
    raise RuntimeError(f"Unsupported source file type: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sources",
        nargs="+",
        default=[str(DEFAULT_SOURCE)],
        help="Chronicles source files (txt, md, json or pdf).",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output path for normalized source events JSON.",
    )
    parser.add_argument(
        "--pdf-sidecar-dir",
        default=str(DEFAULT_SIDECAR_DIR),
        help="Directory with OCR text sidecars for scanned PDFs.",
    )
    parser.add_argument(
        "--allow-inferred-dates",
        action="store_true",
        default=True,
        help="Infer dates when year is missing in paragraph.",
    )
    parser.add_argument(
        "--min-paragraph-length",
        type=int,
        default=140,
        help="Minimum paragraph length for fallback candidate extraction.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing an existing output file.",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    sidecar_dir = Path(args.pdf_sidecar_dir)
    if output_path.exists() and not args.overwrite:
        print(f"ERROR: output already exists: {output_path}. Use --overwrite.")
        return 1

    used_ids: set[str] = set()
    all_events: list[dict] = []
    next_index = 1

    try:
        for src in args.sources:
            source_path = Path(src)
            if not source_path.exists():
                raise RuntimeError(f"Source file not found: {source_path}")
            events = extract_events_from_source(
                source_path,
                next_index,
                used_ids,
                sidecar_dir,
                args.allow_inferred_dates,
                args.min_paragraph_length,
            )
            all_events.extend(events)
            next_index += len(events)

        if len(all_events) < 2:
            raise RuntimeError("Strict mode requires at least 2 source events.")

        all_events.sort(key=lambda ev: ev["chronology_index"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(all_events, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        print(f"Extracted {len(all_events)} events -> {output_path}")
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
