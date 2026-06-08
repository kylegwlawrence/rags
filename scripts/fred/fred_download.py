#!/usr/bin/env python3

"""
FRED Economic Data Downloader
Hybrid approach:
  - Table 1: series — metadata + description for embedding (vector search)
  - Table 2: observations — raw numeric time series for SQL lookup

Get a free API key at: https://fred.stlouisfed.org/docs/api/api_key.html
Set FRED_API_KEY env var or pass --api-key.
Requires: requests
"""

import argparse
import os
import sqlite3
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_DB = "data/fred/fred.db"
DEFAULT_DELAY = 0.1  # FRED allows ~120 req/min
API_BASE = "https://api.stlouisfed.org/fred"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download FRED economic data into SQLite.")
    p.add_argument("--db", default=DEFAULT_DB, help="Output DB path (default: %(default)s)")
    p.add_argument(
        "--api-key",
        default=os.environ.get("FRED_API_KEY"),
        help="FRED API key (default: $FRED_API_KEY)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max series to fetch observations for (default: all)",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help="Seconds between requests (default: %(default)s)",
    )
    p.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate all tables before running",
    )
    return p.parse_args()


def create_schema(con: sqlite3.Connection) -> None:
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            category_id INTEGER PRIMARY KEY,
            parent_id   INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS series (
            series_id           TEXT PRIMARY KEY,
            title               TEXT,
            frequency           TEXT,
            units               TEXT,
            seasonal_adjustment TEXT,
            notes               TEXT,
            observation_start   TEXT,
            observation_end     TEXT,
            popularity          INTEGER,
            category_id         INTEGER,
            source              TEXT
        )
    """)
    # Composite PK covers series_id lookups; separate date index serves range scans.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            series_id TEXT,
            date      TEXT,
            value     REAL,
            PRIMARY KEY (series_id, date)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_obs_date ON observations(date)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ingest_state (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    con.commit()


def api_get(
    session: requests.Session,
    endpoint: str,
    params: dict,
    api_key: str,
    delay: float,
    max_retries: int = 5,
) -> dict:
    """GET an API endpoint, retrying on 429 with Retry-After backoff."""
    params = dict(params)
    params["api_key"] = api_key
    params["file_type"] = "json"
    url = f"{API_BASE}/{endpoint}"
    for attempt in range(max_retries):
        r = session.get(url, params=params, timeout=60)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 60))
            print(f"  Rate limited — sleeping {wait}s (attempt {attempt + 1}/{max_retries})")
            time.sleep(wait)
            continue
        r.raise_for_status()
        time.sleep(delay)
        return r.json()
    raise RuntimeError(f"Gave up after {max_retries} retries: {endpoint} {params}")


def fetch_category_tree(
    session: requests.Session, api_key: str, delay: float, con: sqlite3.Connection
) -> list[int]:
    """
    Return all FRED category IDs. Fetches from API on first run and caches in the
    'categories' table; subsequent runs return the cached list immediately.
    """
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM categories")
    if cur.fetchone()[0] > 0:
        print("  Using cached category tree.")
        cur.execute("SELECT category_id FROM categories")
        return [row[0] for row in cur.fetchall()]

    print("  Fetching category tree from API...")
    cur.execute("INSERT OR IGNORE INTO categories (category_id, parent_id) VALUES (0, NULL)")

    queue: list[int] = [0]
    all_ids: list[int] = [0]

    while queue:
        parent_id = queue.pop(0)
        try:
            data = api_get(session, "category/children", {"category_id": parent_id}, api_key, delay)
            for cat in data.get("categories", []):
                cid = int(cat["id"])
                cur.execute(
                    "INSERT OR IGNORE INTO categories (category_id, parent_id) VALUES (?, ?)",
                    (cid, parent_id),
                )
                all_ids.append(cid)
                queue.append(cid)
        except Exception as e:
            print(f"  Warning: failed to fetch children of category {parent_id}: {e}")

    con.commit()
    print(f"  Found {len(all_ids)} categories.")
    return all_ids


def fetch_series_page(
    session: requests.Session,
    api_key: str,
    delay: float,
    category_id: int,
    offset: int,
) -> tuple[list[dict], int]:
    """Fetch one page of series metadata for a category. Returns (items, total_count)."""
    try:
        data = api_get(
            session,
            "category/series",
            {
                "category_id": category_id,
                "limit": 1000,
                "offset": offset,
                "order_by": "popularity",
                "sort_order": "desc",
            },
            api_key,
            delay,
        )
        return data.get("seriess", []), data.get("count", 0)
    except Exception as e:
        print(f"  Warning: failed to fetch series for category {category_id} offset {offset}: {e}")
        return [], 0


def fetch_observations(
    session: requests.Session, api_key: str, delay: float, series_id: str
) -> list[dict]:
    """Fetch all observations for a series. Returns [] on error."""
    try:
        data = api_get(
            session,
            "series/observations",
            {
                "series_id": series_id,
                "observation_start": "1776-07-04",
                "observation_end": "9999-12-31",
            },
            api_key,
            delay,
        )
        return data.get("observations", [])
    except Exception as e:
        print(f"  Warning: failed to fetch observations for {series_id}: {e}")
        return []


def main() -> None:
    args = parse_args()

    if not args.api_key:
        print("Error: FRED API key required. Set $FRED_API_KEY or pass --api-key.")
        sys.exit(1)

    db_dir = os.path.dirname(args.db)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    con = sqlite3.connect(args.db)
    try:
        if args.reset:
            cur = con.cursor()
            for table in ("categories", "series", "observations", "ingest_state"):
                cur.execute(f"DROP TABLE IF EXISTS {table}")
            con.commit()
            print("Tables dropped — rebuilding from scratch.\n")

        create_schema(con)
        session = requests.Session()
        cur = con.cursor()

        # --- Step 1: build category tree ---
        print("=== Step 1: Category tree ===")
        categories = fetch_category_tree(session, args.api_key, args.delay, con)
        print(f"  {len(categories)} categories total\n")

        # --- Step 2: series metadata (written incrementally per category) ---
        print("=== Step 2: Series metadata ===")
        series_upserted = 0
        for i, cat_id in enumerate(categories):
            cur.execute(
                "SELECT value FROM ingest_state WHERE key = ?", (f"cat_series_done_{cat_id}",)
            )
            if cur.fetchone():
                continue

            offset = 0
            while True:
                series_list, total = fetch_series_page(
                    session, args.api_key, args.delay, cat_id, offset
                )
                if not series_list:
                    break
                for s in series_list:
                    cur.execute(
                        """
                        INSERT OR REPLACE INTO series
                            (series_id, title, frequency, units, seasonal_adjustment,
                             notes, observation_start, observation_end, popularity,
                             category_id, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            s.get("id"),
                            s.get("title"),
                            s.get("frequency_short"),
                            s.get("units"),
                            s.get("seasonal_adjustment_short"),
                            s.get("notes"),
                            s.get("observation_start"),
                            s.get("observation_end"),
                            s.get("popularity"),
                            cat_id,
                            s.get("source"),
                        ),
                    )
                    series_upserted += 1
                offset += len(series_list)
                if offset >= total:
                    break

            cur.execute(
                "INSERT OR REPLACE INTO ingest_state (key, value) VALUES (?, '1')",
                (f"cat_series_done_{cat_id}",),
            )
            con.commit()

            if i % 50 == 0:
                print(f"  {i}/{len(categories)} categories — {series_upserted} series upserted")

        cur.execute("SELECT COUNT(*) FROM series")
        total_series = cur.fetchone()[0]
        print(f"  Total series in DB: {total_series}\n")

        # --- Step 3: observations ---
        print("=== Step 3: Observations ===")
        cur.execute("SELECT series_id FROM series ORDER BY popularity DESC")
        series_ids = [row[0] for row in cur.fetchall()]
        if args.limit is not None:
            series_ids = series_ids[: args.limit]
            print(f"  Limiting to {args.limit} series (--limit)\n")

        total_obs = 0
        for i, series_id in enumerate(series_ids, 1):
            state_key = f"obs_done_{series_id}"
            cur.execute("SELECT 1 FROM ingest_state WHERE key = ?", (state_key,))
            done = cur.fetchone() is not None
            if not done:  # fallback for DBs predating the marker
                cur.execute(
                    "SELECT 1 FROM observations WHERE series_id = ? LIMIT 1", (series_id,)
                )
                done = cur.fetchone() is not None
            if done:
                continue

            obs = fetch_observations(session, args.api_key, args.delay, series_id)
            count = 0
            for o in obs:
                val = o.get("value")
                try:
                    val = float(val)
                except (TypeError, ValueError):
                    val = None  # FRED uses "." for missing values
                cur.execute(
                    "INSERT OR IGNORE INTO observations (series_id, date, value) VALUES (?, ?, ?)",
                    (series_id, o.get("date"), val),
                )
                count += 1

            # Mark fetched even when empty, so a series with no observations
            # isn't re-fetched on every run.
            cur.execute(
                "INSERT OR REPLACE INTO ingest_state (key, value) VALUES (?, '1')",
                (state_key,),
            )
            con.commit()
            total_obs += count

            if i % 500 == 0:
                print(f"  {i}/{len(series_ids)} series — {total_obs} observations total")

        print("\nDone.")
        print(f"  Series in DB:                {total_series}")
        print(f"  New observations this run:   {total_obs}")
        print(f"  DB: {args.db}")

    finally:
        con.close()


if __name__ == "__main__":
    main()
