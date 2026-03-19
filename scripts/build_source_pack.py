#!/usr/bin/env python3
"""Build a reviewed source pack from a canonical source file."""

from __future__ import annotations

import argparse
import hashlib
import re
import statistics
import subprocess
from pathlib import Path

from extract_source_events import (
    YEAR_RE,
    clean_title,
    detect_actors,
    detect_location,
    infer_base_year,
    infer_year_for_index,
    paragraph_to_event,
    repair_common_ocr_issues,
    sanitize_event_text,
    strip_historiography_tail,
)
from pipeline_common import ROOT, normalize_year_token, now_iso, read_json, sha256_text, slugify, source_ref_file, trim_text, write_json, write_text

DEFAULT_SOURCE = ROOT / "docs" / "chronicles" / "01-La gran aventura del reino de Asturias.pdf"
DEFAULT_OUTPUT = ROOT / "data" / "source" / "source_pack.json"
DEFAULT_EVENTS_OUTPUT = ROOT / "data" / "timeline" / "source_events.json"
DEFAULT_ARTIFACTS = ROOT / "artifacts" / "source_pack"

PAGE_TAG_RE = re.compile(r"^\[PAGE\s+\d+\]$", re.IGNORECASE)
PURE_PAGE_RE = re.compile(r"^\s*[—-]?\d{1,4}[—-]?\s*$")
SMART_QUOTES_RE = re.compile(r"[“”‘’«»]")
SHORT_LINE_RE = re.compile(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9][^.!?]{0,42}$")
TERMINAL_PUNCT_RE = re.compile(r"[.!?:;…]$")


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def ensure_program(name: str) -> None:
    if run(["bash", "-lc", f"command -v {name}"]).returncode != 0:
        raise RuntimeError(f"Required program not found: {name}")


def file_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".md":
        return "md"
    if suffix == ".json":
        return "json"
    return "txt"


def rasterize_pdf(pdf_path: Path, pages_dir: Path, start_page: int, end_page: int) -> list[Path]:
    ensure_program("mutool")
    pages_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(pages_dir / "page-%04d.png")
    cmd = ["mutool", "draw", "-q", "-r", "300", "-F", "png", "-o", pattern, str(pdf_path), f"{start_page}-{end_page}"]
    proc = run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"mutool rasterization failed: {(proc.stderr or '').strip()}")
    files = sorted(pages_dir.glob("page-*.png"))
    if not files:
        raise RuntimeError("No rasterized pages were produced from the PDF.")
    return files


def parse_tsv_confidence(tsv_text: str) -> float:
    confidences: list[float] = []
    for line in tsv_text.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 11:
            continue
        try:
            conf = float(parts[10])
        except ValueError:
            continue
        if conf >= 0:
            confidences.append(conf / 100.0)
    if not confidences:
        return 0.0
    return round(statistics.fmean(confidences), 4)


def ocr_image(image_path: Path, ocr_dir: Path) -> dict:
    ensure_program("tesseract")
    stem = image_path.stem
    txt_path = ocr_dir / f"{stem}.txt"
    tsv_path = ocr_dir / f"{stem}.tsv"

    txt_proc = run(["tesseract", str(image_path), "stdout", "-l", "spa", "--psm", "6"])
    if txt_proc.returncode != 0:
        raise RuntimeError(f"Tesseract text OCR failed for {image_path}: {(txt_proc.stderr or '').strip()}")
    write_text(txt_path, txt_proc.stdout)

    tsv_proc = run(["tesseract", str(image_path), "stdout", "-l", "spa", "--psm", "6", "tsv"])
    if tsv_proc.returncode != 0:
        raise RuntimeError(f"Tesseract TSV OCR failed for {image_path}: {(tsv_proc.stderr or '').strip()}")
    write_text(tsv_path, tsv_proc.stdout)

    return {
        "ocr_text_path": txt_path,
        "ocr_tsv_path": tsv_path,
        "raw_text": txt_proc.stdout,
        "ocr_confidence": parse_tsv_confidence(tsv_proc.stdout),
    }


def direct_text_pages(source_path: Path) -> list[dict]:
    raw = source_path.read_text(encoding="utf-8", errors="ignore")
    if source_path.suffix.lower() == ".json":
        payload = read_json(source_path)
        if isinstance(payload, list):
            raw = "\n\n".join(str(item.get("summary", "")).strip() for item in payload if isinstance(item, dict))
        elif isinstance(payload, dict):
            raw = str(payload.get("summary", "")).strip()
    raw = raw.replace("\r", "\n")
    page_parts = [part.strip() for part in re.split(r"\f+|\n\s*\[PAGE\s+\d+\]\s*\n", raw) if part.strip()]
    if not page_parts:
        page_parts = [raw.strip()]
    pages = []
    for index, text in enumerate(page_parts, start=1):
        pages.append(
            {
                "page_number": index,
                "image_path": None,
                "ocr_text_path": None,
                "ocr_tsv_path": None,
                "raw_text": text,
                "ocr_confidence": 1.0,
            }
        )
    return pages


def normalize_margin_key(line: str) -> str:
    key = repair_common_ocr_issues(line).lower()
    key = re.sub(r"\d+", "#", key)
    key = SMART_QUOTES_RE.sub('"', key)
    key = re.sub(r"[^a-z0-9# ]+", " ", key)
    return re.sub(r"\s+", " ", key).strip()


def repeated_margin_candidates(pages: list[dict]) -> tuple[set[str], set[str]]:
    head_counts: dict[str, int] = {}
    foot_counts: dict[str, int] = {}
    for page in pages:
        lines = [line.strip() for line in str(page["raw_text"]).splitlines() if line.strip()]
        if not lines:
            continue
        head = normalize_margin_key(lines[0])
        foot = normalize_margin_key(lines[-1])
        if 4 <= len(head) <= 100:
            head_counts[head] = head_counts.get(head, 0) + 1
        if 4 <= len(foot) <= 100:
            foot_counts[foot] = foot_counts.get(foot, 0) + 1
    threshold = 2 if len(pages) < 6 else 3
    repeated_heads = {key for key, count in head_counts.items() if count >= threshold}
    repeated_feet = {key for key, count in foot_counts.items() if count >= threshold}
    return repeated_heads, repeated_feet


def page_has_column_break(lines: list[str]) -> bool:
    content = [line for line in lines if line.strip()]
    if len(content) < 18:
        return False
    short_ratio = sum(1 for line in content if SHORT_LINE_RE.match(line.strip())) / max(1, len(content))
    lower_starts = sum(1 for line in content[1:] if line[:1].islower()) / max(1, len(content) - 1)
    return short_ratio >= 0.55 and lower_starts >= 0.35


def normalize_page_text(raw_text: str, repeated_heads: set[str], repeated_feet: set[str]) -> tuple[list[str], list[str]]:
    lines = []
    removed_issues: list[str] = []
    for idx, raw in enumerate(raw_text.translate(str.maketrans({"—": "-", "–": "-", "\u00a0": " "})).splitlines()):
        line = raw.strip()
        if not line:
            lines.append("")
            continue
        if PAGE_TAG_RE.match(line) or PURE_PAGE_RE.match(line):
            continue
        margin_key = normalize_margin_key(line)
        if idx == 0 and margin_key in repeated_heads:
            removed_issues.append("repeated_header_removed")
            continue
        if margin_key in repeated_feet:
            removed_issues.append("repeated_footer_removed")
            continue
        line = repair_common_ocr_issues(line)
        line = line.replace("\t", " ")
        line = re.sub(r"\s+", " ", line).strip()
        lines.append(line)
    if page_has_column_break(lines):
        removed_issues.append("possible_column_break")
    return lines, sorted(set(removed_issues))


def lines_to_paragraphs(lines: list[str]) -> list[str]:
    paragraphs: list[str] = []
    current = ""
    for line in lines:
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
            continue
        if TERMINAL_PUNCT_RE.search(current) and line[:1].isupper():
            current += "\n\n" + line
            continue
        current += " " + line
    if current:
        paragraphs.append(current.strip())

    out: list[str] = []
    for paragraph in paragraphs:
        for piece in re.split(r"\n\s*\n", paragraph):
            clean = sanitize_event_text(piece)
            if clean:
                out.append(clean)
    return out


def guess_heading(paragraph: str) -> str | None:
    title = clean_title(paragraph)
    if len(title.split()) < 3:
        return None
    return trim_text(title, 120)


def build_chunks(source_path: Path, pages: list[dict], review_threshold: float) -> list[dict]:
    chunks: list[dict] = []
    last_chunk: dict | None = None
    chunk_index = 1
    for page in pages:
        for paragraph in page["paragraphs"]:
            clean = trim_text(strip_historiography_tail(repair_common_ocr_issues(paragraph)), 3900)
            if len(clean.split()) < 8:
                continue
            should_merge = bool(
                last_chunk
                and not TERMINAL_PUNCT_RE.search(str(last_chunk["normalized_text"]))
                and clean[:1].islower()
            )
            if should_merge and last_chunk:
                last_chunk["text"] = trim_text(f"{last_chunk['text']} {clean}", 3900)
                last_chunk["normalized_text"] = last_chunk["text"]
                last_chunk["page_end"] = page["page_number"]
                last_chunk["ocr_confidence"] = round(
                    (float(last_chunk["ocr_confidence"]) + float(page["ocr_confidence"])) / 2.0,
                    4,
                )
                last_chunk["checksum"] = sha256_text(last_chunk["normalized_text"])
                page["chunk_ids"].append(last_chunk["chunk_id"])
                continue

            issues = list(page["issues"])
            if float(page["ocr_confidence"]) < review_threshold:
                issues.append("low_ocr_confidence")
            chunk = {
                "chunk_id": f"chk-{chunk_index:04d}",
                "file": source_ref_file(source_path),
                "page_start": page["page_number"],
                "page_end": page["page_number"],
                "heading": guess_heading(clean),
                "checksum": sha256_text(clean),
                "ocr_confidence": round(float(page["ocr_confidence"]), 4),
                "review_status": "needs_review" if issues else "approved",
                "issues": sorted(set(issues)),
                "text": clean,
                "normalized_text": clean,
            }
            chunks.append(chunk)
            page["chunk_ids"].append(chunk["chunk_id"])
            last_chunk = chunk
            chunk_index += 1
    return chunks


def build_pages(source_path: Path, artifacts_root: Path, start_page: int, end_page: int, review_threshold: float) -> list[dict]:
    if source_path.suffix.lower() == ".pdf":
        pages_dir = artifacts_root / "pages"
        ocr_dir = artifacts_root / "ocr"
        image_paths = rasterize_pdf(source_path, pages_dir, start_page, end_page)
        pages = []
        for image_path in image_paths:
            ocr_payload = ocr_image(image_path, ocr_dir)
            page_number = int(image_path.stem.split("-")[-1])
            pages.append(
                {
                    "page_number": page_number,
                    "image_path": str(image_path.relative_to(ROOT)),
                    "ocr_text_path": str(ocr_payload["ocr_text_path"].relative_to(ROOT)),
                    "ocr_tsv_path": str(ocr_payload["ocr_tsv_path"].relative_to(ROOT)),
                    "raw_text": str(ocr_payload["raw_text"]),
                    "ocr_confidence": float(ocr_payload["ocr_confidence"]),
                }
            )
    else:
        pages = direct_text_pages(source_path)

    repeated_heads, repeated_feet = repeated_margin_candidates(pages)
    final_pages: list[dict] = []
    for page in pages:
        lines, issues = normalize_page_text(str(page["raw_text"]), repeated_heads, repeated_feet)
        paragraphs = lines_to_paragraphs(lines)
        if not paragraphs:
            issues.append("empty_after_cleanup")
        review_status = "needs_review" if issues or float(page["ocr_confidence"]) < review_threshold else "approved"
        final_pages.append(
            {
                "page_number": int(page["page_number"]),
                "image_path": page.get("image_path"),
                "ocr_text_path": page.get("ocr_text_path"),
                "ocr_tsv_path": page.get("ocr_tsv_path"),
                "ocr_confidence": round(float(page["ocr_confidence"]), 4),
                "review_status": review_status,
                "chunk_ids": [],
                "issues": sorted(set(issues)),
                "excerpt": trim_text(" ".join(paragraphs[:2]) or "Sin contenido legible tras la limpieza.", 260),
                "paragraphs": paragraphs,
            }
        )
    return final_pages


def chronology_hints(chunks: list[dict]) -> list[dict]:
    hints = []
    for chunk in chunks:
        years = sorted({year for year in (normalize_year_token(raw) for raw in YEAR_RE.findall(str(chunk["normalized_text"]))) if year})
        hints.append(
            {
                "chunk_id": chunk["chunk_id"],
                "summary": trim_text(str(chunk["normalized_text"]), 220),
                "detected_years": years,
            }
        )
    return hints


def derive_events(source_path: Path, chunks: list[dict]) -> list[dict]:
    combined_text = "\n\n".join(str(chunk["normalized_text"]) for chunk in chunks)
    base_year = infer_base_year(combined_text)
    used_ids: set[str] = set()
    events: list[dict] = []
    for index, chunk in enumerate(chunks, start=1):
        paragraph = trim_text(str(chunk["normalized_text"]), 1500)
        if len(paragraph.split()) < 10:
            continue
        year_match = YEAR_RE.search(paragraph)
        year = normalize_year_token(year_match.group(1)) if year_match else normalize_year_token(infer_year_for_index(base_year, index))
        event = paragraph_to_event(
            paragraph=paragraph,
            source_ref_file=source_ref_file(source_path),
            section=chunk["chunk_id"],
            index=index,
            used_ids=used_ids,
            base_year=base_year,
            allow_inferred_dates=True,
        )
        event["date_start"] = year
        event["title"] = clean_title(paragraph)
        event["actors"] = detect_actors(paragraph)
        event["location"] = detect_location(paragraph)
        event["summary"] = trim_text(strip_historiography_tail(paragraph), 1500)
        event["source_ref"]["section"] = chunk["chunk_id"]
        event["source_ref"]["checksum"] = chunk["checksum"]
        events.append(event)
    return events


def source_pack_payload(source_path: Path, pages: list[dict], chunks: list[dict], derived_events: list[dict]) -> dict:
    slug = slugify(source_path.stem)
    input_checksum = sha256_text("\n".join(str(chunk["checksum"]) for chunk in chunks))
    return {
        "source_pack_id": f"spk-{slug}",
        "title": source_path.stem,
        "language": "es",
        "created_at": now_iso(),
        "review": {
            "status": "pending",
            "reviewer": None,
            "reviewed_at": None,
            "notes": "Revisar OCR y aprobar manualmente antes de generar personajes o historias.",
        },
        "generation_meta": {
            "pipeline_version": "source-pack-v1",
            "input_checksum": input_checksum,
        },
        "source_documents": [
            {
                "file": source_ref_file(source_path),
                "kind": file_kind(source_path),
                "checksum": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                "page_count": len(pages),
                "ocr_mode": "ocr" if source_path.suffix.lower() == ".pdf" else "direct_text",
            }
        ],
        "pages": [
            {
                "page_number": page["page_number"],
                "image_path": page["image_path"],
                "ocr_text_path": page["ocr_text_path"],
                "ocr_tsv_path": page["ocr_tsv_path"],
                "ocr_confidence": page["ocr_confidence"],
                "review_status": page["review_status"],
                "chunk_ids": page["chunk_ids"],
                "issues": page["issues"],
                "excerpt": page["excerpt"],
            }
            for page in pages
        ],
        "chunks": chunks,
        "chronology_hints": chronology_hints(chunks),
        "derived_events": derived_events,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Canonical source file (.pdf/.txt/.md/.json)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output source_pack.json path")
    parser.add_argument("--derived-events-output", default=str(DEFAULT_EVENTS_OUTPUT), help="Derived source events output path")
    parser.add_argument("--artifacts-root", default=str(DEFAULT_ARTIFACTS), help="Artifacts root for OCR and raster pages")
    parser.add_argument("--start-page", type=int, default=1, help="First page for PDF OCR")
    parser.add_argument("--end-page", type=int, default=120, help="Last page for PDF OCR")
    parser.add_argument("--review-threshold", type=float, default=0.8, help="OCR confidence threshold that triggers review")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting existing outputs")
    args = parser.parse_args()

    source_path = Path(args.source)
    output_path = Path(args.output)
    derived_events_output = Path(args.derived_events_output)
    artifacts_root = Path(args.artifacts_root) / slugify(source_path.stem)

    if not source_path.exists():
        print(f"ERROR: source file not found: {source_path}")
        return 1
    if (output_path.exists() or derived_events_output.exists()) and not args.overwrite:
        print("ERROR: output already exists. Use --overwrite to replace files.")
        return 1

    try:
        pages = build_pages(source_path, artifacts_root, args.start_page, args.end_page, args.review_threshold)
        chunks = build_chunks(source_path, pages, args.review_threshold)
        if not chunks:
            raise RuntimeError("No usable chunks were extracted from the source.")
        derived_events = derive_events(source_path, chunks)
        if not derived_events:
            raise RuntimeError("No derived events could be produced from the source pack.")
        payload = source_pack_payload(source_path, pages, chunks, derived_events)
        write_json(output_path, payload)
        write_json(derived_events_output, derived_events)
        print(f"Generated source pack: {output_path}")
        print(f"Derived events: {derived_events_output}")
        print(f"Chunks: {len(chunks)}")
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
