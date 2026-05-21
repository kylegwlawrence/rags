#!/usr/bin/env python3
"""Download LOC Chronicling America newspaper metadata via loc.gov API into SQLite."""

import argparse
import math
import os
import sqlite3
import time
from typing import Optional

import requests

DEFAULT_DB = "./data/loc/loc_newspapers.db"
DEFAULT_DATE_FROM = "1770-01-01"  # Chronicling America coverage start
DEFAULT_DATE_TO = "1963-12-31"    # Chronicling America coverage end
BASE_URL = "https://www.loc.gov/collections/chronicling-america/"
PER_PAGE = 100
MAX_RETRIES = 3
# LOC bulk API limit is ~10 requests per 10 minutes
REQUEST_DELAY = 7


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS newspapers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id         TEXT UNIQUE,
            title           TEXT,
            date            TEXT,
            newspaper_title TEXT,
            state           TEXT,
            city            TEXT,
            language        TEXT,
            url             TEXT,
            snippet         TEXT
        );
        CREATE TABLE IF NOT EXISTS ingest_state (
            id                  INTEGER PRIMARY KEY CHECK (id = 1),
            last_completed_page INTEGER
        );
        INSERT OR IGNORE INTO ingest_state (id, last_completed_page) VALUES (1, NULL);
    """)


def get_last_completed_page(cur: sqlite3.Cursor) -> Optional[int]:
    row = cur.execute("SELECT last_completed_page FROM ingest_state WHERE id = 1").fetchone()
    return row[0] if row else None


def fetch_page(page: int, date_from: str, date_to: str) -> dict:
    """Fetch one page from the Chronicling America API, looping on 429."""
    params = {
        "fo": "json",
        "c": PER_PAGE,
        "sp": page,
        "dates": f"{date_from}/{date_to}",
        "fa": "language:english",
        "at": "results,pagination",
    }
    while True:
        response = requests.get(BASE_URL, params=params, timeout=60)
        if response.status_code == 429:
            print("Rate limited — sleeping 60s")
            time.sleep(60)
            continue
        response.raise_for_status()
        return response.json()


def parse_item(item: dict) -> tuple:
    """Extract and normalize fields from a single LOC API result."""
    item_id  = item.get("id", "")
    title    = item.get("title", "")
    date     = item.get("date", "")
    url      = item.get("url", "")
    language = ", ".join(item.get("language", []))

    location = item.get("location_city", [])
    city     = location[0] if location else ""
    state_list = item.get("location_state", [])
    state    = state_list[0] if state_list else ""

    partof = item.get("partof", [])
    first  = partof[0] if partof else None
    newspaper_title = first.get("title", "") if isinstance(first, dict) else title

    snippet = item.get("description", "") or item.get("summary", "") or ""
    if isinstance(snippet, list):
        snippet = " ".join(snippet)

    return (item_id, title, date, newspaper_title, state, city, language, url, snippet)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download LOC Chronicling America newspaper metadata into SQLite."
    )
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"Path to SQLite database (default: {DEFAULT_DB})")
    parser.add_argument("--date-from", default=DEFAULT_DATE_FROM,
                        help=f"Start date YYYY-MM-DD (default: {DEFAULT_DATE_FROM})")
    parser.add_argument("--date-to", default=DEFAULT_DATE_TO,
                        help=f"End date YYYY-MM-DD (default: {DEFAULT_DATE_TO})")
    args = parser.parse_args()

    db_dir = os.path.dirname(args.db)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    create_schema(cur)
    con.commit()

    last = get_last_completed_page(cur)
    start_page = (last + 1) if last is not None else 1
    if last is not None:
        print(f"Resuming from page {start_page} (last completed: {last})")

    total_inserted = 0
    page = start_page

    print("Starting Chronicling America download...")

    while True:
        print(f"Fetching page {page}...")

        data = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                data = fetch_page(page, args.date_from, args.date_to)
                break
            except requests.RequestException as e:
                print(f"  Error on page {page} (attempt {attempt}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(5 * attempt)

        if data is None:
            print(f"  Giving up on page {page} after {MAX_RETRIES} attempts — stopping.")
            break

        results = data.get("results", [])
        if not results:
            print("No more results.")
            break

        for item in results:
            cur.execute("""
                INSERT OR IGNORE INTO newspapers
                (item_id, title, date, newspaper_title, state, city, language, url, snippet)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, parse_item(item))
            total_inserted += cur.rowcount

        con.commit()
        cur.execute("UPDATE ingest_state SET last_completed_page = ? WHERE id = 1", (page,))
        con.commit()

        print(f"  Page {page} done — total inserted: {total_inserted}")

        pagination  = data.get("pagination", {})
        total_items = pagination.get("total", 0)
        total_pages = math.ceil(total_items / PER_PAGE) if total_items else page
        if page >= total_pages:
            break
        page += 1
        time.sleep(REQUEST_DELAY)

    con.close()
    print(f"\nDone. Total records inserted: {total_inserted}")


if __name__ == "__main__":
    main()
