#!/usr/bin/env python3
"""Download Federal Register documents into a local SQLite database."""

import argparse
import datetime
import json
import os
import sqlite3
import time
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_DB = "./data/federal_register/federal_register.db"
START_YEAR = 1994  # Federal Register API coverage starts here
BASE_URL = "https://www.federalregister.gov/api/v1/documents.json"
MAX_RETRIES = 3
REQUEST_DELAY = 1  # seconds between pages (~60 req/min polite limit)
FIELDS = [
    "document_number", "title", "abstract", "type", "publication_date",
    "agencies", "action", "dates", "effective_on", "cfr_references",
    "html_url", "pdf_url", "raw_text_url", "excerpts",
]


def create_schema(cur: sqlite3.Cursor) -> None:
    """Create tables if they don't exist."""
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            document_number  TEXT PRIMARY KEY,
            title            TEXT,
            abstract         TEXT,
            type             TEXT,
            publication_date TEXT,
            agencies         TEXT,
            action           TEXT,
            dates            TEXT,
            effective_date   TEXT,
            cfr_references   TEXT,
            html_url         TEXT,
            pdf_url          TEXT,
            raw_text_url     TEXT,
            excerpts         TEXT
        );
        CREATE TABLE IF NOT EXISTS ingest_state (
            id                   INTEGER PRIMARY KEY CHECK (id = 1),
            last_completed_year  INTEGER,
            last_completed_page  INTEGER
        );
        INSERT OR IGNORE INTO ingest_state (id, last_completed_year, last_completed_page)
        VALUES (1, NULL, NULL);
    """)


def get_ingest_state(cur: sqlite3.Cursor) -> tuple[Optional[int], Optional[int]]:
    row = cur.execute(
        "SELECT last_completed_year, last_completed_page FROM ingest_state WHERE id = 1"
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def fetch_page(session: requests.Session, year: int, page: int) -> dict:
    """Fetch one page of documents for a given year, with retry on errors."""
    params = [
        ("conditions[publication_date][year]", year),
        ("per_page", 1000),
        ("page", page),
        ("order", "oldest"),
    ]
    for field in FIELDS:
        params.append(("fields[]", field))

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(BASE_URL, params=params, timeout=60)
        except requests.RequestException as e:
            print(f"  Request error (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(5 * attempt)
                continue
            raise

        if resp.status_code == 429:
            print("  Rate limited — sleeping 60 s")
            time.sleep(60)
            # Don't count 429 as an attempt; just retry immediately
            continue

        if resp.status_code != 200:
            print(f"  HTTP {resp.status_code} (attempt {attempt}/{MAX_RETRIES}): {resp.text[:200]}")
            if attempt < MAX_RETRIES:
                time.sleep(5 * attempt)
                continue
            resp.raise_for_status()

        return resp.json()

    raise RuntimeError("fetch_page: unreachable")  # pragma: no cover


def parse_doc(doc: dict) -> tuple:
    """Extract and normalise fields from a single Federal Register API result."""
    agencies = ", ".join(a.get("name", "") for a in doc.get("agencies", []))

    excerpts = doc.get("excerpts", "")
    if isinstance(excerpts, list):
        excerpts = " ".join(excerpts)
    excerpts = excerpts or ""

    return (
        doc.get("document_number"),
        doc.get("title"),
        doc.get("abstract"),
        doc.get("type"),
        doc.get("publication_date"),
        agencies,
        doc.get("action"),
        json.dumps(doc.get("dates") or {}),
        doc.get("effective_on"),
        json.dumps(doc.get("cfr_references") or []),
        doc.get("html_url"),
        doc.get("pdf_url"),
        doc.get("raw_text_url"),
        excerpts,
    )


def main() -> None:
    current_year = datetime.date.today().year

    parser = argparse.ArgumentParser(description="Download Federal Register documents into SQLite.")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"Path to SQLite database (default: {DEFAULT_DB})")
    parser.add_argument("--year-from", type=int, default=None,
                        help=f"Start year (default: resume from last run, or {START_YEAR})")
    parser.add_argument("--year-to", type=int, default=current_year,
                        help=f"End year inclusive (default: {current_year})")
    args = parser.parse_args()

    email = os.environ.get("DATASETS_EMAIL")
    if not email:
        parser.error("DATASETS_EMAIL env var is required for the User-Agent contact address.")

    db_dir = os.path.dirname(args.db)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    create_schema(cur)
    con.commit()

    last_year, last_page = get_ingest_state(cur)

    if args.year_from is not None:
        year_from = args.year_from
        start_page = 1
    elif last_year is not None:
        # Resume mid-year if we have a page watermark, otherwise next year
        if last_page is not None:
            year_from = last_year
            start_page = last_page + 1
            print(f"Resuming year {year_from} from page {start_page}")
        else:
            year_from = last_year + 1
            start_page = 1
            print(f"Resuming from year {year_from} (last completed: {last_year})")
    else:
        year_from = START_YEAR
        start_page = 1

    session = requests.Session()
    session.headers["User-Agent"] = f"datasets-bot/1.0 (mailto:{email})"

    total_inserted = 0

    for year in range(year_from, args.year_to + 1):
        print(f"\n=== Year {year} ===")
        page = start_page if year == year_from else 1

        while True:
            try:
                data = fetch_page(session, year, page)
            except (requests.RequestException, RuntimeError) as e:
                print(f"  Giving up on year {year} page {page}: {e} — stopping.")
                con.close()
                return

            results = data.get("results", [])
            if not results:
                break

            inserted = 0
            for doc in results:
                cur.execute("""
                    INSERT OR IGNORE INTO documents
                    (document_number, title, abstract, type, publication_date,
                     agencies, action, dates, effective_date, cfr_references,
                     html_url, pdf_url, raw_text_url, excerpts)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, parse_doc(doc))
                inserted += cur.rowcount

            con.commit()
            # Advance page watermark after each successful page
            cur.execute(
                "UPDATE ingest_state SET last_completed_year = ?, last_completed_page = ? WHERE id = 1",
                (year, page),
            )
            con.commit()

            total_inserted += inserted
            total_pages = data.get("total_pages", 1)
            print(f"  Page {page}/{total_pages}: {inserted} inserted (run total: {total_inserted})")

            if page >= total_pages:
                break
            page += 1
            time.sleep(REQUEST_DELAY)

        # Year complete — clear the page watermark so next run starts at page 1
        cur.execute(
            "UPDATE ingest_state SET last_completed_year = ?, last_completed_page = NULL WHERE id = 1",
            (year,),
        )
        con.commit()

    con.close()
    print(f"\nDone. Total documents inserted: {total_inserted}")


if __name__ == "__main__":
    main()
