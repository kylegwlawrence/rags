#!/usr/bin/env python3
"""Build an FTS5 index over places.name/country_name/feature_description for free-text search.

Backfills feature_description on existing DBs that predate the column.
Re-runnable: drops `places_fts` and rebuilds from scratch.
Restart uvicorn after — the API caches the source connection at import time.
"""

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

sys.path.insert(0, str(REPO_ROOT))

from rag.fts import run_fts_indexer  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "geonames" / "geonames.db"
FEATURE_CODES_PATH = REPO_ROOT / "data" / "geonames" / "feature_codes.csv"


def _load_feature_descriptions(path: Path) -> dict[str, str]:
    """Build 'class.code' → description lookup from feature_codes.csv."""
    import csv
    descs: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = f"{row['feature_class']}.{row['feature_code']}"
            descs[key] = row["description"]
    return descs


def _backfill_feature_description(db_path: Path, feature_codes_path: Path) -> None:
    """Add feature_description column to places and populate it if missing."""
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    # Check whether the column already exists.
    cols = {row[1] for row in cur.execute("PRAGMA table_info(places)")}
    if "feature_description" not in cols:
        print("Adding feature_description column...")
        cur.execute("ALTER TABLE places ADD COLUMN feature_description TEXT")
        con.commit()

    # Check if any rows need populating.
    unfilled = cur.execute(
        "SELECT COUNT(*) FROM places WHERE feature_description IS NULL"
    ).fetchone()[0]
    if unfilled == 0:
        con.close()
        return

    print(f"Backfilling feature_description for {unfilled:,} rows...")
    descs = _load_feature_descriptions(feature_codes_path)

    # Build a temporary lookup table and join-update rather than a Python loop.
    cur.execute("CREATE TEMP TABLE _fc (key TEXT PRIMARY KEY, desc TEXT)")
    cur.executemany(
        "INSERT INTO _fc VALUES (?, ?)", descs.items()
    )
    cur.execute("""
        UPDATE places
        SET feature_description = (
            SELECT desc FROM _fc
            WHERE _fc.key = places.feature_class || '.' || places.feature_code
        )
        WHERE feature_description IS NULL
    """)
    con.commit()
    con.close()
    print("Backfill complete.")


if __name__ == "__main__":
    if not DB_PATH.is_file():
        print(f"missing: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    if not FEATURE_CODES_PATH.is_file():
        print(f"missing: {FEATURE_CODES_PATH}", file=sys.stderr)
        sys.exit(1)

    _backfill_feature_description(DB_PATH, FEATURE_CODES_PATH)

    sys.exit(run_fts_indexer(
        db_path=DB_PATH,
        virtual_table="places_fts",
        content_table="places",
        columns=("name", "country_name", "feature_description"),
        content_rowid="geonameid",
        row_label="places",
    ))
