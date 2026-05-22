#!/usr/bin/env python3
"""
World Bank Indicators Downloader

Downloads all topic-tagged indicator metadata and observations (start-year to
present) from the World Bank Indicators API v2 into SQLite. No API key required.

Phases:
  1. Topics        — 21 development topics
  2. Countries     — all economies (countries + regional/income aggregates)
  3. Indicators    — metadata for every indicator in at least one topic
  4. Observations  — non-null values per indicator, all economies, date range

Resumable: completed indicators are tracked in `completed_indicators`; re-run
after interruption and only the remaining indicators will be fetched.

Usage:
    python scripts/worldbank/worldbank_download.py
    python scripts/worldbank/worldbank_download.py --start-year 2020
    python scripts/worldbank/worldbank_download.py --reset
"""

import argparse
import datetime
import os
import sqlite3
import time
from typing import Optional

import requests

BASE_URL = "https://api.worldbank.org/v2"
DELAY = 0.3         # seconds between requests — WB API has no hard rate limit but be polite
MAX_RETRIES = 3
PER_PAGE = 1000


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS topics (
            id   INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS countries (
            id           TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            region       TEXT,
            income_level TEXT,
            capital_city TEXT,
            longitude    REAL,
            latitude     REAL
        );

        CREATE TABLE IF NOT EXISTS indicators (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            unit        TEXT,
            source_note TEXT,
            source_org  TEXT
        );

        CREATE TABLE IF NOT EXISTS indicator_topics (
            indicator_id TEXT    NOT NULL REFERENCES indicators(id),
            topic_id     INTEGER NOT NULL REFERENCES topics(id),
            PRIMARY KEY (indicator_id, topic_id)
        );

        CREATE TABLE IF NOT EXISTS observations (
            indicator_id TEXT    NOT NULL,
            country_id   TEXT    NOT NULL,
            year         INTEGER NOT NULL,
            value        REAL    NOT NULL,
            PRIMARY KEY (indicator_id, country_id, year)
        );

        CREATE INDEX IF NOT EXISTS idx_obs_country   ON observations(country_id);
        CREATE INDEX IF NOT EXISTS idx_obs_indicator ON observations(indicator_id);
        CREATE INDEX IF NOT EXISTS idx_obs_year      ON observations(year);

        CREATE TABLE IF NOT EXISTS completed_indicators (
            indicator_id TEXT PRIMARY KEY
        );
    """)


def fetch_json(session: requests.Session, url: str, params: dict) -> Optional[list]:
    """GET a WB API URL and return the parsed JSON list, or None on failure."""
    params = {**params, "format": "json"}
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(url, params=params, timeout=60)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 60))
                print(f"  Rate limited — sleeping {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            if attempt < MAX_RETRIES - 1:
                print(f"  Request error: {exc}, retrying in 5s...")
                time.sleep(5)
            else:
                print(f"  Failed after {MAX_RETRIES} attempts: {exc}")
    return None


def fetch_paginated(session: requests.Session, url: str, params: dict) -> list:
    """Fetch all pages from a WB API endpoint and return the combined item list."""
    all_items: list = []
    page = 1
    while True:
        data = fetch_json(session, url, {**params, "page": page, "per_page": PER_PAGE})
        if data is None or len(data) < 2 or data[1] is None:
            break
        meta = data[0]
        all_items.extend(data[1])
        time.sleep(DELAY)
        if page >= meta.get("pages", 1):
            break
        page += 1
    return all_items


def _none_if_blank(v: Optional[str]) -> Optional[str]:
    """Normalise empty strings from the API to None."""
    return v if v else None


def main() -> None:
    current_year = datetime.date.today().year
    parser = argparse.ArgumentParser(
        description="Download World Bank topic-tagged indicator data into SQLite"
    )
    parser.add_argument("--db", default="data/worldbank/worldbank.db",
                        help="Output SQLite path (default: data/worldbank/worldbank.db)")
    parser.add_argument("--start-year", type=int, default=2021,
                        help="First year to fetch observations for (default: 2021)")
    parser.add_argument("--reset", action="store_true",
                        help="Drop and recreate all tables before downloading")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.db), exist_ok=True)

    con = sqlite3.connect(args.db)
    cur = con.cursor()

    if args.reset:
        print("Resetting database...")
        cur.executescript("""
            DROP TABLE IF EXISTS completed_indicators;
            DROP TABLE IF EXISTS observations;
            DROP TABLE IF EXISTS indicator_topics;
            DROP TABLE IF EXISTS indicators;
            DROP TABLE IF EXISTS countries;
            DROP TABLE IF EXISTS topics;
        """)
        con.commit()

    create_schema(cur)
    con.commit()

    session = requests.Session()
    session.headers.update({"User-Agent": "worldbank-downloader kylegwlawrence@gmail.com"})

    # -------------------------------------------------------------------------
    # Phase 1: Topics
    # -------------------------------------------------------------------------
    print("Phase 1: Fetching topics...")
    topics = fetch_paginated(session, f"{BASE_URL}/topic", {})
    for t in topics:
        cur.execute(
            "INSERT OR IGNORE INTO topics (id, name) VALUES (?, ?)",
            (int(t["id"]), t["value"]),
        )
    con.commit()
    print(f"  {len(topics)} topics stored")

    # -------------------------------------------------------------------------
    # Phase 2: Countries (all economies, including regional/income aggregates)
    # -------------------------------------------------------------------------
    print("Phase 2: Fetching countries and aggregates...")
    countries = fetch_paginated(session, f"{BASE_URL}/country", {})
    for c in countries:
        try:
            lon = float(c["longitude"]) if c.get("longitude") else None
            lat = float(c["latitude"]) if c.get("latitude") else None
        except (ValueError, TypeError):
            lon = lat = None
        cur.execute(
            """INSERT OR IGNORE INTO countries
               (id, name, region, income_level, capital_city, longitude, latitude)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                c["id"],
                c["name"],
                _none_if_blank(c.get("region", {}).get("value")),
                _none_if_blank(c.get("incomeLevel", {}).get("value")),
                _none_if_blank(c.get("capitalCity")),
                lon,
                lat,
            ),
        )
    con.commit()
    print(f"  {len(countries)} economies stored")

    # -------------------------------------------------------------------------
    # Phase 3: Indicators (union of all topic-tagged indicators)
    # -------------------------------------------------------------------------
    print("Phase 3: Fetching indicators by topic...")
    indicator_topics: list[tuple[str, int]] = []
    seen_indicator_ids: set[str] = set()

    for t in topics:
        topic_id = int(t["id"])
        indicators = fetch_paginated(
            session, f"{BASE_URL}/topic/{topic_id}/indicator", {}
        )
        print(f"  Topic {topic_id:2d} ({t['value']}): {len(indicators)} indicators")
        for ind in indicators:
            ind_id = ind["id"]
            indicator_topics.append((ind_id, topic_id))
            if ind_id not in seen_indicator_ids:
                seen_indicator_ids.add(ind_id)
                cur.execute(
                    """INSERT OR IGNORE INTO indicators
                       (id, name, unit, source_note, source_org)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        ind_id,
                        ind.get("name", ""),
                        _none_if_blank(ind.get("unit")),
                        _none_if_blank(ind.get("sourceNote")),
                        _none_if_blank(ind.get("sourceOrganization")),
                    ),
                )

    cur.executemany(
        "INSERT OR IGNORE INTO indicator_topics (indicator_id, topic_id) VALUES (?, ?)",
        indicator_topics,
    )
    con.commit()
    print(f"  {len(seen_indicator_ids)} unique indicators stored")

    # -------------------------------------------------------------------------
    # Phase 4: Observations
    # -------------------------------------------------------------------------
    completed = {
        row[0]
        for row in cur.execute(
            "SELECT indicator_id FROM completed_indicators"
        ).fetchall()
    }
    remaining = sorted(seen_indicator_ids - completed)
    end_year = current_year
    print(
        f"\nPhase 4: Fetching observations ({args.start_year}–{end_year}) "
        f"for {len(remaining)} indicators ({len(completed)} already done)..."
    )

    total_obs = 0
    for i, ind_id in enumerate(remaining, 1):
        rows = fetch_paginated(
            session,
            f"{BASE_URL}/country/all/indicator/{ind_id}",
            {"date": f"{args.start_year}:{end_year}"},
        )
        obs_rows = []
        for r in rows:
            if r.get("value") is None:
                continue
            country = r.get("country") or {}
            country_id = country.get("id")
            date_str = r.get("date", "")
            # Skip non-annual entries (e.g. "2021Q1" for quarterly data)
            if not date_str.isdigit() or len(date_str) != 4:
                continue
            if not country_id:
                continue
            obs_rows.append((ind_id, country_id, int(date_str), r["value"]))

        if obs_rows:
            cur.executemany(
                """INSERT OR IGNORE INTO observations
                   (indicator_id, country_id, year, value)
                   VALUES (?, ?, ?, ?)""",
                obs_rows,
            )
            total_obs += len(obs_rows)

        cur.execute(
            "INSERT OR IGNORE INTO completed_indicators (indicator_id) VALUES (?)",
            (ind_id,),
        )
        con.commit()

        if i % 50 == 0 or i == len(remaining):
            print(
                f"  [{i:>{len(str(len(remaining)))}}/{len(remaining)}] "
                f"{total_obs:,} observations so far"
            )

    con.close()
    print(f"\nDone. {total_obs:,} total observations.")
    print(f"DB: {args.db}")


if __name__ == "__main__":
    main()
