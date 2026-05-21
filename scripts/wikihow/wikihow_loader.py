#!/usr/bin/env python3
"""Load the wikihowSep.csv dataset into a local SQLite database.

`wikihowSep.csv` is the per-step ("separated") form of the wikiHow corpus:
one CSV row per step, several rows sharing a guide `title`. Each row carries
the guide-level `overview` (repeated on every step of the guide), the
per-section `sectionLabel` (e.g. "Using Home Remedies"), the step `headline`
(its bolded summary sentence) and the step `text`.

Rows are stored one-per-step in `articles`; the autoincrement `id` preserves
CSV order, which is step order within a guide. The RAG extractor reconstructs
whole guides by grouping on `title` and ordering by `id`.
"""

import argparse
import csv
import os
import sqlite3
import sys

DEFAULT_DB = "./data/wikihow/wikihow.db"
DEFAULT_CSV = "./data/wikihow/wikihowSep.csv"


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            title         TEXT,
            section_label TEXT,
            headline      TEXT,
            overview      TEXT,
            text          TEXT,
            UNIQUE (title, section_label, headline)
        );

        -- The UNIQUE constraint's implicit index is keyed on
        -- (title, section_label, headline), so its leftmost column is `title`
        -- and it can't serve a standalone `section_label = ?` lookup. The
        -- /wikihow/articles?section_label= filter needs its own index.
        CREATE INDEX IF NOT EXISTS idx_articles_section_label
            ON articles (section_label);
    """)


def main() -> None:
    parser = argparse.ArgumentParser(description="Load wikihowSep.csv into SQLite.")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"Path to SQLite database (default: {DEFAULT_DB})")
    parser.add_argument("--csv", default=DEFAULT_CSV,
                        help=f"Path to wikihowSep.csv (default: {DEFAULT_CSV})")
    args = parser.parse_args()

    if not os.path.exists(args.csv):
        print(f"ERROR: {args.csv} not found.")
        print("Download wikihowAll.csv from the wikiHow dataset page.")
        sys.exit(1)

    db_dir = os.path.dirname(args.db)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    # sys.maxsize overflows the C long limit on Linux; cap at 2^31-1
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    create_schema(cur)
    con.commit()

    total = 0
    skipped = 0
    with open(args.csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            headline = (row.get("headline") or "").strip()
            title = (row.get("title") or "").strip()
            text = (row.get("text") or "").strip()
            section_label = (row.get("sectionLabel") or "").strip()
            overview = (row.get("overview") or "").strip()

            if not text and not headline:
                skipped += 1
                continue

            cur.execute("""
                INSERT OR IGNORE INTO articles
                    (title, section_label, headline, overview, text)
                VALUES (?, ?, ?, ?, ?)
            """, (title, section_label, headline, overview, text))
            total += 1

            if total % 1000 == 0:
                con.commit()
                print(f"Inserted {total} articles...")

    con.commit()
    con.close()
    print(f"\nDone. {total} articles processed, {skipped} skipped ({args.db})")


if __name__ == "__main__":
    main()
