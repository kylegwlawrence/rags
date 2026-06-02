#!/usr/bin/env python3
"""Merge papers from a staging DB into per-parent-category shards.

After a successful merge the staging DB's paper data (papers, authors,
paper_authors) is wiped so the next harvest starts clean.  The ingest_state
table is preserved so arxiv_ingest.py knows where to resume.

Usage::

    python scripts/arxiv/arxiv_merge_shards.py
    python scripts/arxiv/arxiv_merge_shards.py \\
        --staging data/arxiv/staging.db \\
        --shards-dir data/arxiv/shards
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from arxiv_ingest import connect, upsert_paper, _has_legacy_authors_column  # noqa: E402

DEFAULT_STAGING = REPO_ROOT / "data" / "arxiv" / "staging.db"
DEFAULT_SHARDS_DIR = REPO_ROOT / "data" / "arxiv" / "shards"

# SQL expression: parent category = text before the first ".", or the whole
# code for legacy dotless codes like alg-geom.
_PARENT_EXPR = (
    "CASE WHEN instr(primary_category, '.') > 0 "
    "THEN substr(primary_category, 1, instr(primary_category, '.') - 1) "
    "ELSE primary_category END"
)


def _build_record(paper_row: sqlite3.Row, author_rows: list[sqlite3.Row]) -> dict[str, Any]:
    """Combine a papers row and its author rows into the dict upsert_paper expects."""
    record = dict(paper_row)
    record["authors"] = [
        {
            "keyname": a["keyname"],
            "forenames": a["forenames"],
            "display_name": a["display_name"],
            "affiliation": a["affiliation"],
        }
        for a in author_rows
    ]
    return record


def merge(staging_path: Path, shards_dir: Path) -> dict[str, dict[str, int]]:
    """Merge all papers in staging into the appropriate shard. Returns stats per parent."""
    src = sqlite3.connect(f"file:{staging_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    # Group paper IDs by parent category.
    parent_map: dict[str, list[str]] = {}
    for row in src.execute(f"SELECT id, {_PARENT_EXPR} AS parent FROM papers"):
        parent_map.setdefault(row["parent"], []).append(row["id"])

    if not parent_map:
        print("  No papers in staging DB — nothing to merge.", file=sys.stderr)
        src.close()
        return {}

    stats: dict[str, dict[str, int]] = {}

    for parent, paper_ids in sorted(parent_map.items()):
        shard_path = shards_dir / f"{parent}.db"
        if not shard_path.exists():
            print(f"  {parent}: shard not found — skipping", file=sys.stderr)
            continue

        dst = connect(shard_path)
        legacy = _has_legacy_authors_column(dst)
        counts: dict[str, int] = {"inserted": 0, "updated": 0, "skipped": 0}

        for paper_id in paper_ids:
            paper_row = src.execute(
                "SELECT * FROM papers WHERE id = ?", (paper_id,)
            ).fetchone()
            author_rows = src.execute(
                "SELECT a.* FROM authors a "
                "JOIN paper_authors pa ON pa.author_id = a.id "
                "WHERE pa.paper_id = ? ORDER BY pa.position",
                (paper_id,),
            ).fetchall()
            record = _build_record(paper_row, author_rows)
            action = upsert_paper(dst, record, legacy_authors_column=legacy)
            counts[action] += 1

        dst.commit()
        dst.close()
        stats[parent] = counts
        print(
            f"  {parent:12s}: "
            f"+{counts['inserted']} inserted  "
            f"{counts['updated']} updated  "
            f"{counts['skipped']} skipped",
            file=sys.stderr,
        )

    src.close()
    return stats


def wipe_staging(staging_path: Path) -> None:
    """Delete paper data from staging, preserving ingest_state for the next harvest."""
    conn = sqlite3.connect(staging_path)
    conn.executescript(
        "DELETE FROM paper_authors; DELETE FROM authors; DELETE FROM papers;"
    )
    conn.commit()
    conn.close()


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staging",
        type=Path,
        default=DEFAULT_STAGING,
        help=f"Staging DB written by arxiv_ingest.py (default: {DEFAULT_STAGING.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--shards-dir",
        type=Path,
        default=DEFAULT_SHARDS_DIR,
        help=f"Directory containing per-parent shard DBs (default: {DEFAULT_SHARDS_DIR.relative_to(REPO_ROOT)})",
    )
    args = parser.parse_args(argv)

    if not args.staging.exists():
        print(f"Staging DB not found: {args.staging}", file=sys.stderr)
        return 1

    if not args.shards_dir.is_dir():
        print(f"Shards directory not found: {args.shards_dir}", file=sys.stderr)
        return 1

    print(f"Merging {args.staging} → {args.shards_dir}/...", file=sys.stderr)
    stats = merge(args.staging, args.shards_dir)

    total_new = sum(s["inserted"] + s["updated"] for s in stats.values())
    print(
        f"Merged {total_new} new/updated papers across {len(stats)} shards.",
        file=sys.stderr,
    )

    print("Wiping staging DB paper data...", file=sys.stderr)
    wipe_staging(args.staging)
    print("Done.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
