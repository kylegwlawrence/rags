#!/usr/bin/env python3

"""
NOAA GHCNd (Global Historical Climatology Network daily) Downloader
Downloads all station metadata and daily observations by year into SQLite.
Data goes back to 1763, covering 100,000+ stations globally.
Requires: requests
"""

import argparse
import csv
import datetime
import gzip
import io
import os
import sqlite3
import time
from typing import Optional

import requests

GHCND_BASE = "https://www.ncei.noaa.gov/pub/data/ghcn/daily"
DELAY = 0.5
MAX_RETRIES = 3

# Core elements to store; others are too sparse for general use.
# Unit notes (GHCNd stores most in tenths of the unit, but NOT all):
#   TMAX, TMIN, TAVG → tenths of °C     → convert_value divides by 10
#   PRCP, AWND       → tenths of mm/m·s → convert_value divides by 10
#   SNOW, SNWD       → already in mm    → no conversion
#   AWDR             → whole degrees    → no conversion
KEEP_ELEMENTS = {"TMAX", "TMIN", "PRCP", "SNOW", "SNWD", "TAVG", "AWND", "AWDR"}


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS stations (
            station_id   TEXT PRIMARY KEY,
            latitude     REAL,
            longitude    REAL,
            elevation    REAL,
            state        TEXT,
            name         TEXT,
            country_code TEXT
        );
        CREATE TABLE IF NOT EXISTS observations (
            station_id TEXT NOT NULL,
            date       TEXT NOT NULL,
            element    TEXT NOT NULL,
            value      REAL,
            PRIMARY KEY (station_id, date, element)
        );
        CREATE INDEX IF NOT EXISTS idx_obs_date    ON observations(date);
        CREATE INDEX IF NOT EXISTS idx_obs_element ON observations(element);
        CREATE TABLE IF NOT EXISTS ingest_state (
            id                  INTEGER PRIMARY KEY CHECK (id = 1),
            last_completed_year INTEGER
        );
        INSERT OR IGNORE INTO ingest_state (id, last_completed_year)
        VALUES (1, NULL);
    """)


def get_last_year(cur: sqlite3.Cursor) -> Optional[int]:
    """Return the last successfully completed year, or None if starting fresh."""
    row = cur.execute(
        "SELECT last_completed_year FROM ingest_state WHERE id = 1"
    ).fetchone()
    return row[0] if row else None


def set_last_year(cur: sqlite3.Cursor, year: int) -> None:
    cur.execute(
        "UPDATE ingest_state SET last_completed_year = ? WHERE id = 1", (year,)
    )


def convert_value(element: str, raw: str) -> Optional[float]:
    """
    Convert a raw GHCNd string value to its standard unit.

    Returns None if the value cannot be parsed (missing / trace markers).
    """
    try:
        val = float(raw)
    except ValueError:
        return None
    # Only these elements are stored in tenths of the unit.
    if element in ("TMAX", "TMIN", "TAVG", "PRCP", "AWND"):
        return val / 10.0
    # SNOW and SNWD are already in mm; AWDR is in whole degrees.
    return val


def download_stations(
    session: requests.Session,
    cur: sqlite3.Cursor,
    con: sqlite3.Connection,
) -> None:
    """Download station metadata and insert any new stations."""
    print("Downloading station metadata...")
    r = session.get(f"{GHCND_BASE}/ghcnd-stations.txt", timeout=60)
    r.raise_for_status()

    rows: list[tuple[str, Optional[float], Optional[float], Optional[float], str, str, str]] = []
    for line in r.text.splitlines():
        if len(line) < 38:
            continue
        station_id = line[0:11].strip()
        try:
            lat: Optional[float] = float(line[12:20].strip())
            lon: Optional[float] = float(line[21:30].strip())
            elev: Optional[float] = float(line[31:37].strip())
        except ValueError:
            lat = lon = elev = None
        state = line[38:40].strip() if len(line) > 40 else ""
        name = line[41:71].strip() if len(line) > 71 else ""
        country_code = station_id[:2]
        rows.append((station_id, lat, lon, elev, state, name, country_code))

    cur.executemany(
        """
        INSERT OR IGNORE INTO stations
            (station_id, latitude, longitude, elevation, state, name, country_code)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    con.commit()
    print(f"  {len(rows)} stations processed.")


def fetch_year_content(session: requests.Session, year: int) -> Optional[bytes]:
    """
    Download the compressed observation file for a year.
    Returns None on 404 (year not yet published).
    Raises on other HTTP errors after MAX_RETRIES attempts.
    """
    url = f"{GHCND_BASE}/by_year/{year}.csv.gz"
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(url, timeout=300)
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 60))
                print(f"  Rate limited — sleeping {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.content
        except requests.RequestException as exc:
            if attempt < MAX_RETRIES - 1:
                print(f"  Request error: {exc}, retrying...")
                time.sleep(5)
            else:
                raise
    return None


def parse_year_content(content: bytes) -> list[tuple[str, str, str, float]]:
    """
    Decompress and parse a year's observation CSV.
    Returns (station_id, date, element, value) tuples for elements in KEEP_ELEMENTS.
    """
    rows: list[tuple[str, str, str, float]] = []
    with gzip.open(io.BytesIO(content), "rt", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 4:
                continue
            station_id, date, element, raw_value = row[0], row[1], row[2], row[3]
            if element not in KEEP_ELEMENTS:
                continue
            value = convert_value(element, raw_value)
            if value is None:
                continue
            rows.append((station_id, date, element, value))
    return rows


def main() -> None:
    current_year = datetime.date.today().year
    parser = argparse.ArgumentParser(
        description="Download NOAA GHCNd daily observations into SQLite"
    )
    parser.add_argument("--db", default="data/noaa/noaa_ghcnd.db")
    parser.add_argument(
        "--start-year",
        type=int,
        default=1763,
        help="First year to fetch (default: 1763)",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=current_year,
        help="Last year to fetch (default: current year)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after N years (default: all)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate tables before downloading",
    )
    parser.add_argument(
        "--email",
        default=os.environ.get("NOAA_EMAIL", "kylegwlawrence@gmail.com"),
        help="Contact email for User-Agent header (or set NOAA_EMAIL env var)",
    )
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.db), exist_ok=True)

    con = sqlite3.connect(args.db)
    cur = con.cursor()

    if args.reset:
        cur.executescript("""
            DROP TABLE IF EXISTS stations;
            DROP TABLE IF EXISTS observations;
            DROP TABLE IF EXISTS ingest_state;
        """)
        con.commit()

    create_schema(cur)
    con.commit()

    resume_year = get_last_year(cur)
    if resume_year:
        print(f"Resuming from after {resume_year}")

    session = requests.Session()
    session.headers.update({
        "User-Agent": f"noaa-ghcnd-fetcher (contact: {args.email})"
    })

    download_stations(session, cur, con)

    years = [
        y for y in range(args.start_year, args.end_year + 1)
        if resume_year is None or y > resume_year
    ]
    if args.limit is not None:
        years = years[: args.limit]

    print(f"\nDownloading observations for {len(years)} years "
          f"({years[0] if years else '—'}–{years[-1] if years else '—'})...")
    total_obs = 0

    for year in years:
        print(f"  Year {year}...")
        try:
            content = fetch_year_content(session, year)
        except Exception as exc:
            print(f"    Error fetching: {exc} — skipping")
            time.sleep(5)
            continue

        if content is None:
            print("    Not found — skipping")
            set_last_year(cur, year)
            con.commit()
            time.sleep(DELAY)
            continue

        try:
            obs_rows = parse_year_content(content)
        except Exception as exc:
            print(f"    Parse error: {exc} — skipping")
            continue

        cur.executemany(
            """
            INSERT OR IGNORE INTO observations (station_id, date, element, value)
            VALUES (?, ?, ?, ?)
            """,
            obs_rows,
        )
        set_last_year(cur, year)
        con.commit()

        total_obs += len(obs_rows)
        print(f"    {len(obs_rows):,} observations (running total: {total_obs:,})")
        time.sleep(DELAY)

    con.close()
    print(f"\nDone. Total observations written: {total_obs:,}")
    print(f"DB: {args.db}")
    print("Elements: TMAX/TMIN/TAVG (°C), PRCP/AWND (÷10 to m·s⁻¹), "
          "SNOW/SNWD (mm), AWDR (°)")


if __name__ == "__main__":
    main()
