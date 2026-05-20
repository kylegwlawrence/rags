#!/usr/bin/env python3
"""Download LOC manuscript metadata + descriptions via loc.gov API into SQLite."""

import argparse
import math
import os
import sqlite3
import time
from typing import Optional

import requests

DEFAULT_DB = "./data/loc/loc_manuscripts.db"
BASE_URL = "https://www.loc.gov/collections/manuscript-division/"
PER_PAGE = 100
MAX_RETRIES = 3


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS manuscripts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id     TEXT UNIQUE,
            title       TEXT,
            date        TEXT,
            creator     TEXT,
            subject     TEXT,
            description TEXT,
            language    TEXT,
            collection  TEXT,
            url         TEXT
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


def fetch_page(page: int) -> dict:
    """Fetch one page from the LOC manuscripts API, looping on 429."""
    params = {
        "fo": "json",
        "c": PER_PAGE,
        "sp": page,
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
    url      = item.get("url", "")
    language = ", ".join(item.get("language", []))
    dates    = item.get("date", "") or ""

    creators = item.get("contributor", []) or item.get("creator", [])
    creator  = "; ".join(creators) if isinstance(creators, list) else str(creators)

    subjects = item.get("subject", [])
    subject  = "; ".join(subjects) if isinstance(subjects, list) else str(subjects)

    desc = item.get("description", "") or item.get("summary", "") or ""
    if isinstance(desc, list):
        desc = " ".join(desc)

    partof     = item.get("partof", [])
    first      = partof[0] if partof else None
    collection = first.get("title", "") if isinstance(first, dict) else ""

    return (item_id, title, dates, creator, subject, desc, language, collection, url)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download LOC manuscript metadata into SQLite."
    )
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"Path to SQLite database (default: {DEFAULT_DB})")
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

    print("Starting manuscripts download...")

    while True:
        print(f"Fetching page {page}...")

        data = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                data = fetch_page(page)
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
                INSERT OR IGNORE INTO manuscripts
                (item_id, title, date, creator, subject, description, language, collection, url)
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
        time.sleep(1)

    con.close()
    print(f"\nDone. Total records inserted: {total_inserted}")


if __name__ == "__main__":
    main()
