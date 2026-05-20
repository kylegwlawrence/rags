#!/usr/bin/env python3
"""Download Federal Register documents into a local SQLite database."""

import argparse
import json
import os
import sqlite3
import time
from typing import Optional

import requests

DEFAULT_DB = "./data/federal_register/federal_register.db"
START_YEAR = 1994  # Federal Register API coverage starts here
DEFAULT_END_YEAR = 2026
BASE_URL = "https://www.federalregister.gov/api/v1/documents.json"
FIELDS = [
    "document_number", "title", "abstract", "type", "publication_date",
    "agencies", "action", "dates", "effective_on", "cfr_references",
    "html_url", "pdf_url", "raw_text_url", "excerpts",
]


def create_schema(cur: sqlite3.Cursor) -> None:
    """Create tables if they don't exist."""
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            document_number TEXT PRIMARY KEY,
            title           TEXT,
            abstract        TEXT,
            type            TEXT,
            publication_date TEXT,
            agencies        TEXT,
            action          TEXT,
            dates           TEXT,
            effective_date  TEXT,
            cfr_references  TEXT,
            html_url        TEXT,
            pdf_url         TEXT,
            raw_text_url    TEXT,
            excerpts        TEXT
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
    parser = argparse.ArgumentParser(description="Download Federal Register documents into SQLite.")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"Path to SQLite database (default: {DEFAULT_DB})")
    parser.add_argument("--year-from", type=int, default=None,
                        help=f"Start year (default: resume from last run, or {START_YEAR})")
    parser.add_argument("--year-to", type=int, default=DEFAULT_END_YEAR,
                        help=f"End year inclusive (default: {DEFAULT_END_YEAR})")
    args = parser.parse_args()

    db_dir = os.path.dirname(args.db)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    create_schema(cur)
    con.commit()

    # Determine starting year — explicit flag overrides resume watermark
    if args.year_from is not None:
        year_from = args.year_from
    else:
        last = get_last_completed_year(cur)
        year_from = (last + 1) if last is not None else START_YEAR
        if last is not None:
            print(f"Resuming from year {year_from} (last completed: {last})")

    total_inserted = 0
    errors = 0

    for year in range(year_from, args.year_to + 1):
        print(f"\n=== Year {year} ===")
        page = 1
        year_error = False

        while True:
            params = [
                ("conditions[publication_date][year]", year),
                ("per_page", 1000),
                ("page", page),
                ("order", "oldest"),
            ]
            for field in FIELDS:
                params.append(("fields[]", field))

            try:
                response = requests.get(BASE_URL, params=params, timeout=60)
            except requests.RequestException as e:
                print(f"Request failed: {e} — retrying in 30s")
                time.sleep(30)
                continue

            if response.status_code == 429:
                print("Rate limited — sleeping 60s")
                time.sleep(60)
                continue
            if response.status_code != 200:
                print(f"Error {response.status_code}: {response.text[:200]}")
                year_error = True
                errors += 1
                break

            data = response.json()
            results = data.get("results", [])
            if not results:
                break

            for doc in results:
                agencies = ", ".join(a.get("name", "") for a in doc.get("agencies", []))
                cur.execute("""
                    INSERT OR IGNORE INTO documents
                    (document_number, title, abstract, type, publication_date,
                     agencies, action, dates, effective_date, cfr_references,
                     html_url, pdf_url, raw_text_url, excerpts)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    doc.get("document_number"),
                    doc.get("title"),
                    doc.get("abstract"),
                    doc.get("type"),
                    doc.get("publication_date"),
                    agencies,
                    doc.get("action"),
                    json.dumps(doc.get("dates")),       # dict → JSON string
                    doc.get("effective_on"),
                    json.dumps(doc.get("cfr_references", [])),
                    doc.get("html_url"),
                    doc.get("pdf_url"),
                    doc.get("raw_text_url"),
                    doc.get("excerpts") or "",
                ))

            con.commit()
            total_inserted += len(results)
            print(f"  Year {year} page {page}: {len(results)} docs (total: {total_inserted})")

            total_pages = data.get("total_pages", 1)
            if page >= total_pages:
                break
            page += 1
            time.sleep(1)  # ~60 req/min polite limit

        if not year_error:
            cur.execute(
                "UPDATE ingest_state SET last_completed_year = ? WHERE id = 1", (year,)
            )
            con.commit()

    con.close()
    print(f"\nDone. Total documents processed: {total_inserted} (errors: {errors})")


if __name__ == "__main__":
    main()
