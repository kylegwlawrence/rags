#!/usr/bin/env python3

"""
UN Treaty Collection Downloader
Downloads treaty metadata and text from the UN Treaty Collection API.
Covers all multilateral treaties deposited with the UN Secretary-General.
No API key required.
Requires: requests
"""

import argparse
import os
import sqlite3
import time
from typing import Any

import requests

API_BASE = "https://treaties.un.org/api/v1"
DELAY = 0.5


def fetch_with_retry(
    session: requests.Session,
    url: str,
    params: dict[str, Any] | None = None,
    max_retries: int = 3,
) -> requests.Response | None:
    """GET with 429 backoff and transient-error retry."""
    for attempt in range(max_retries):
        try:
            r = session.get(url, params=params, timeout=60)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 60))
                print(f"  Rate limited — sleeping {wait}s")
                time.sleep(wait)
                continue
            return r
        except requests.RequestException as exc:
            if attempt < max_retries - 1:
                print(f"  Request error: {exc}, retrying...")
                time.sleep(DELAY * 2)
            else:
                print(f"  Request failed after {max_retries} attempts: {exc}")
    return None


def fetch_treaties(
    session: requests.Session, offset: int = 0, limit: int = 100
) -> dict[str, Any] | None:
    r = fetch_with_retry(session, f"{API_BASE}/treaties", params={"offset": offset, "limit": limit})
    if r is None or r.status_code != 200:
        return None
    return r.json()


def fetch_treaty_detail(session: requests.Session, treaty_id: str) -> dict[str, Any]:
    r = fetch_with_retry(session, f"{API_BASE}/treaties/{treaty_id}")
    if r is None:
        return {}
    try:
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        print(f"  Detail fetch failed for {treaty_id}: {exc}")
        return {}


def fetch_treaty_text(session: requests.Session, treaty_id: str) -> str:
    r = fetch_with_retry(session, f"{API_BASE}/treaties/{treaty_id}/text")
    if r is None or r.status_code != 200:
        return ""
    try:
        return r.json().get("text", "") or ""
    except Exception:
        return ""


def fetch_parties(session: requests.Session, treaty_id: str) -> list[dict[str, Any]]:
    r = fetch_with_retry(session, f"{API_BASE}/treaties/{treaty_id}/parties")
    if r is None:
        return []
    try:
        r.raise_for_status()
        return r.json().get("parties", [])
    except Exception as exc:
        print(f"  Parties fetch failed for {treaty_id}: {exc}")
        return []


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS treaties (
            treaty_id TEXT PRIMARY KEY,
            name TEXT,
            place_of_conclusion TEXT,
            date_of_conclusion TEXT,
            date_entry_into_force TEXT,
            depositary TEXT,
            subject TEXT,
            chapter TEXT,
            parties_count INTEGER,
            registration_number TEXT,
            summary TEXT,
            full_text TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS parties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            treaty_id TEXT,
            country TEXT,
            action TEXT,
            action_date TEXT,
            entry_into_force TEXT,
            UNIQUE (treaty_id, country, action, action_date)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_treaty ON parties(treaty_id)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download UN Treaty Collection into SQLite")
    parser.add_argument("--db", default="data/untreaties/un_treaties.db")
    parser.add_argument("--limit", type=int, default=None, help="Stop after N treaties (default: all)")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate tables before downloading")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.db), exist_ok=True)

    con = sqlite3.connect(args.db)
    cur = con.cursor()

    if args.reset:
        cur.execute("DROP TABLE IF EXISTS treaties")
        cur.execute("DROP TABLE IF EXISTS parties")
        con.commit()

    create_schema(cur)
    con.commit()

    session = requests.Session()
    session.headers.update({"User-Agent": "un-treaties-fetcher", "Accept": "application/json"})

    print("Downloading UN Treaty Collection...")
    total_treaties = 0
    total_parties = 0
    offset = 0
    page_limit = 100

    while True:
        if args.limit is not None and total_treaties >= args.limit:
            print(f"  Reached --limit {args.limit}, stopping.")
            break

        print(f"Fetching treaties {offset}–{offset + page_limit}...")
        data = fetch_treaties(session, offset, page_limit)

        if data is None:
            print("  API error — stopping")
            break

        treaties = data.get("treaties", []) or data.get("data", []) or []
        if not treaties:
            print("No more treaties.")
            break

        for treaty in treaties:
            if args.limit is not None and total_treaties >= args.limit:
                break

            treaty_id = str(treaty.get("id") or treaty.get("treaty_id") or "")
            if not treaty_id:
                continue

            cur.execute("SELECT 1 FROM treaties WHERE treaty_id = ?", (treaty_id,))
            if cur.fetchone():
                continue

            detail = fetch_treaty_detail(session, treaty_id)
            full_text = fetch_treaty_text(session, treaty_id)
            time.sleep(0.2)

            name = detail.get("name") or treaty.get("name", "")
            place = detail.get("place_of_conclusion", "")
            date_conclusion = detail.get("date_of_conclusion", "")
            date_eif = detail.get("entry_into_force", "")
            depositary = detail.get("depositary", "")
            subject = detail.get("subject", "")
            chapter = detail.get("chapter", "")
            reg_number = detail.get("registration_number", "")
            summary = detail.get("summary", "") or ""
            parties_count = detail.get("parties_count", 0)

            cur.execute("""
                INSERT OR IGNORE INTO treaties
                (treaty_id, name, place_of_conclusion, date_of_conclusion,
                 date_entry_into_force, depositary, subject, chapter,
                 parties_count, registration_number, summary, full_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                treaty_id, name, place, date_conclusion, date_eif,
                depositary, subject, chapter, parties_count,
                reg_number, summary, full_text,
            ))

            parties = fetch_parties(session, treaty_id)
            for party in parties:
                cur.execute("""
                    INSERT OR IGNORE INTO parties
                    (treaty_id, country, action, action_date, entry_into_force)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    treaty_id,
                    party.get("country", ""),
                    party.get("action", ""),
                    party.get("action_date", ""),
                    party.get("entry_into_force", ""),
                ))
            total_parties += len(parties)
            total_treaties += 1
            time.sleep(DELAY)

        con.commit()
        print(f"  {total_treaties} treaties inserted so far")

        total = data.get("total") or data.get("count") or 0
        offset += page_limit
        if total and offset >= total:
            break
        if len(treaties) < page_limit:
            break

        time.sleep(DELAY)

    con.close()
    print(f"\nDone.")
    print(f"  Treaties: {total_treaties}")
    print(f"  Parties:  {total_parties}")
    print(f"  DB:       {args.db}")
    print("\nNote: UN Treaty API structure may vary. If you get empty results,")
    print("check https://treaties.un.org/api/v1 for current endpoint docs.")


if __name__ == "__main__":
    main()
