#!/usr/bin/env python3
"""Review or approve a source pack before story generation."""

from __future__ import annotations

import argparse
from pathlib import Path

from pipeline_common import now_iso, read_json, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-pack", required=True, help="Path to source_pack.json")
    parser.add_argument("--reviewer", default="human_reviewer", help="Reviewer name")
    parser.add_argument("--notes", default="", help="Review notes")
    parser.add_argument("--approve", action="store_true", help="Mark source pack as approved")
    parser.add_argument("--reject", action="store_true", help="Mark source pack as rejected")
    parser.add_argument("--summary", action="store_true", help="Print a short review summary")
    args = parser.parse_args()

    source_pack_path = Path(args.source_pack)
    if not source_pack_path.exists():
        print(f"ERROR: source pack not found: {source_pack_path}")
        return 1

    payload = read_json(source_pack_path)
    if not isinstance(payload, dict):
        print(f"ERROR: invalid source pack format: {source_pack_path}")
        return 1

    if args.summary or (not args.approve and not args.reject):
        chunks = payload.get("chunks") or []
        needs_review = [chunk for chunk in chunks if chunk.get("review_status") == "needs_review"]
        print(f"source_pack_id: {payload.get('source_pack_id')}")
        print(f"review.status: {payload.get('review', {}).get('status')}")
        print(f"chunks: {len(chunks)}")
        print(f"chunks_needing_review: {len(needs_review)}")
        for chunk in needs_review[:10]:
            issues = ", ".join(chunk.get("issues") or []) or "no_issues_listed"
            print(f"  - {chunk.get('chunk_id')}: {issues}")
        if args.summary or (not args.approve and not args.reject):
            return 0

    status = "approved" if args.approve else "rejected"
    payload["review"] = {
        "status": status,
        "reviewer": args.reviewer,
        "reviewed_at": now_iso(),
        "notes": args.notes or f"Source pack marked as {status} by {args.reviewer}.",
    }
    write_json(source_pack_path, payload)
    print(f"Updated review status to '{status}' in {source_pack_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
