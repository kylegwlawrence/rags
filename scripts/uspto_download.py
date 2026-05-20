#!/usr/bin/env python3
"""Download USPTO patent abstracts from PatentsView into a local SQLite database."""

import argparse
import csv
import io
import os
import sqlite3
import sys
import zipfile
from typing import Optional

import requests

DEFAULT_DB = "./data/patents/patents.db"
DEFAULT_DOWNLOAD_DIR = "./data/patents/raw"
START_YEAR = 2000
DEFAULT_END_YEAR = 2025


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS abstracts (
            patent_id TEXT PRIMARY KEY,
            abstract  TEXT
        );
        CREATE TABLE IF NOT EXISTS ingest_state (
            id                   INTEGER PRIMARY KEY CHECK (id = 1),
            last_completed_year  INTEGER
        );
        INSERT OR IGNORE INTO ingest_state (id, last_completed_year) VALUES (1, NULL);
    """)


def get_last_completed_year(cur: sqlite3.Cursor) -> Optional[int]:
    row = cur.execute("SELECT last_completed_year FROM ingest_state WHERE id = 1").fetchone()
    return row[0] if row else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Download USPTO patent abstracts from PatentsView.")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"Path to SQLite database (default: {DEFAULT_DB})")
    parser.add_argument("--download-dir", default=DEFAULT_DOWNLOAD_DIR,
                        help=f"Directory for downloaded zip files (default: {DEFAULT_DOWNLOAD_DIR})")
    parser.add_argument("--year-from", type=int, default=None,
                        help=f"Start year (default: resume from last run, or {START_YEAR})")
    parser.add_argument("--year-to", type=int, default=DEFAULT_END_YEAR,
                        help=f"End year inclusive (default: {DEFAULT_END_YEAR})")
    args = parser.parse_args()

    db_dir = os.path.dirname(args.db)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    os.makedirs(args.download_dir, exist_ok=True)

    # sys.maxsize overflows the C long limit on Linux; cap at 2^31-1
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    create_schema(cur)
    con.commit()

    if args.year_from is not None:
        year_from = args.year_from
    else:
        last = get_last_completed_year(cur)
        year_from = (last + 1) if last is not None else START_YEAR
        if last is not None:
            print(f"Resuming from year {year_from} (last completed: {last})")

    total_inserted = 0

    for year in range(year_from, args.year_to + 1):
        url = f"https://patentsview.org/download/g_brf_sum_text_{year}.tsv.zip"
        zip_path = os.path.join(args.download_dir, f"g_brf_sum_text_{year}.tsv.zip")
        print(f"\n=== Year {year} ===")
        print(f"Downloading {url}...")

        # Download
        try:
            response = requests.get(url, stream=True, timeout=120)
        except requests.RequestException as e:
            print(f"  Network error: {e} — skipping")
            continue

        if response.status_code == 404:
            print(f"  No data found for {year} — skipping")
            continue
        if response.status_code != 200:
            print(f"  HTTP {response.status_code} — skipping")
            continue

        try:
            with open(zip_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
        except OSError as e:
            print(f"  Failed to write zip: {e} — skipping")
            continue

        # Parse
        year_inserted = 0
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                tsv_name = z.namelist()[0]
                with z.open(tsv_name) as tsv_file:
                    reader = csv.DictReader(
                        io.TextIOWrapper(tsv_file, encoding="utf-8"),
                        delimiter="\t",
                    )
                    for row in reader:
                        patent_id = (row.get("patent_id") or "").strip()
                        abstract = (row.get("text") or "").strip()

                        if not patent_id or not abstract:
                            continue

                        cur.execute("""
                            INSERT OR IGNORE INTO abstracts (patent_id, abstract)
                            VALUES (?, ?)
                        """, (patent_id, abstract))
                        year_inserted += cur.rowcount

                        if year_inserted % 10000 == 0:
                            con.commit()
                            print(f"  {year_inserted} records inserted for {year}...")

        except (zipfile.BadZipFile, UnicodeDecodeError, csv.Error) as e:
            print(f"  Parse error: {e} — skipping")
            os.remove(zip_path)
            continue

        con.commit()
        os.remove(zip_path)
        total_inserted += year_inserted
        print(f"  Done — {year_inserted} abstracts inserted for {year}")

        cur.execute("UPDATE ingest_state SET last_completed_year = ? WHERE id = 1", (year,))
        con.commit()

    con.close()
    print(f"\nDone. Total abstracts inserted: {total_inserted}")


if __name__ == "__main__":
    main()
