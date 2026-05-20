#!/usr/bin/env python3
"""Load the wikihowAll.csv dataset into a local SQLite database."""

import argparse
import csv
import os
import sqlite3
import sys

DEFAULT_DB = "./data/wikihow/wikihow.db"
DEFAULT_CSV = "./data/wikihow/wikihowAll.csv"


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            title    TEXT,
            headline TEXT,
            text     TEXT,
            UNIQUE (title, headline)
        );
    """)


def main() -> None:
    parser = argparse.ArgumentParser(description="Load wikihowAll.csv into SQLite.")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"Path to SQLite database (default: {DEFAULT_DB})")
    parser.add_argument("--csv", default=DEFAULT_CSV,
                        help=f"Path to wikihowAll.csv (default: {DEFAULT_CSV})")
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

            if not text and not headline:
                skipped += 1
                continue

            cur.execute("""
                INSERT OR IGNORE INTO articles (title, headline, text)
                VALUES (?, ?, ?)
            """, (title, headline, text))
            total += 1

            if total % 1000 == 0:
                con.commit()
                print(f"Inserted {total} articles...")

    con.commit()
    con.close()
    print(f"\nDone. {total} articles processed, {skipped} skipped ({args.db})")


if __name__ == "__main__":
    main()
