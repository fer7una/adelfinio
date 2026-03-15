#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

INPUT_PDF="${1:-}"
OUTPUT_TXT="${2:-}"
START_PAGE="${3:-1}"
END_PAGE="${4:-80}"

if [[ -z "$INPUT_PDF" || -z "$OUTPUT_TXT" ]]; then
  echo "Usage: bash scripts/ocr_pdf_to_sidecar.sh <input.pdf> <output.txt> [start_page] [end_page]" >&2
  exit 1
fi

if [[ ! -f "$INPUT_PDF" ]]; then
  echo "ERROR: input PDF not found: $INPUT_PDF" >&2
  exit 1
fi

if ! command -v mutool >/dev/null 2>&1; then
  echo "ERROR: mutool is required for page rasterization." >&2
  exit 1
fi

if ! command -v tesseract >/dev/null 2>&1; then
  echo "ERROR: tesseract is required for OCR. Install it and retry." >&2
  exit 1
fi

TMP_DIR="$ROOT_DIR/.tmp/ocr_$(date +%s)"
mkdir -p "$TMP_DIR"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Rasterizing pages $START_PAGE-$END_PAGE from $INPUT_PDF ..."
mutool draw -q -r 300 -F png -o "$TMP_DIR/page-%04d.png" "$INPUT_PDF" "${START_PAGE}-${END_PAGE}"

: > "$OUTPUT_TXT"
count=0
for img in "$TMP_DIR"/page-*.png; do
  [[ -f "$img" ]] || continue
  page_num="$(basename "$img" .png | sed 's/page-0*//')"
  echo "[PAGE $page_num]" >> "$OUTPUT_TXT"
  tesseract "$img" stdout -l spa --psm 6 2>/dev/null >> "$OUTPUT_TXT"
  echo -e "\n" >> "$OUTPUT_TXT"
  count=$((count + 1))
done

if [[ "$count" -eq 0 ]]; then
  echo "ERROR: no rasterized pages were produced." >&2
  exit 1
fi

echo "OCR completed: $OUTPUT_TXT (pages: $count)"
