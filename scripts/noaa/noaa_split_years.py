#!/usr/bin/env python3
"""
Split noaa_ghcnd.db into one SQLite database per year under data/noaa/years/{year}.db.

Each year DB contains:
  - stations: all station metadata (~100k rows, small)
  - observations: only that year's daily records
  - same indexes as the source, plus an index on station_id

Skips years whose DB already exists. Use --force to overwrite.
Run from the repo root with the venv active:

    python scripts/noaa/noaa_split_years.py
"""

import argparse
import os
import sqlite3

YEAR_SCHEMA = """
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
    CREATE INDEX IF NOT EXISTS idx_obs_station ON observations(station_id);
"""


def get_year_range(db_path: str) -> tuple[int, int]:
    """Return (min_year, max_year) over observations.date."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    min_d, max_d = con.execute(
        "SELECT MIN(date), MAX(date) FROM observations"
    ).fetchone()
    con.close()
    return int(min_d[:4]), int(max_d[:4])


def split_year(src_path: str, year: int, out_path: str) -> int:
    """
    Create out_path, attach src_path as read-only, copy stations and one year of
    observations. Returns the number of observations written.
    """
    dst = sqlite3.connect(out_path)
    try:
        dst.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")
        dst.executescript(YEAR_SCHEMA)

        abs_src = os.path.abspath(src_path)
        dst.execute(f"ATTACH DATABASE 'file:{abs_src}?mode=ro' AS src")

        # Stations table is ~100k rows — copy all of them; avoids an expensive join.
        dst.execute("INSERT OR IGNORE INTO stations SELECT * FROM src.stations")

        # Date range comparison lets SQLite use idx_obs_date for a fast index scan.
        dst.execute(
            """
            INSERT OR IGNORE INTO observations
            SELECT * FROM src.observations
            WHERE date >= ? AND date < ?
            """,
            (f"{year}-01-01", f"{year + 1}-01-01"),
        )
        dst.commit()
        dst.execute("DETACH DATABASE src")

        obs_count: int = dst.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    finally:
        dst.close()
    return obs_count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Split noaa_ghcnd.db into per-year SQLite databases"
    )
    parser.add_argument(
        "--db",
        default="data/noaa/noaa_ghcnd.db",
        help="Source monolithic DB (default: data/noaa/noaa_ghcnd.db)",
    )
    parser.add_argument(
        "--output-dir",
        default="data/noaa/years",
        help="Directory for year DBs (default: data/noaa/years)",
    )
    parser.add_argument("--start-year", type=int, default=None)
    parser.add_argument("--end-year", type=int, default=None)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing year DBs instead of skipping them",
    )
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    print(f"Scanning year range in {args.db}...")
    db_min, db_max = get_year_range(args.db)
    lo = max(db_min, args.start_year) if args.start_year else db_min
    hi = min(db_max, args.end_year) if args.end_year else db_max
    years = list(range(lo, hi + 1))
    print(f"Years to process: {lo}–{hi} ({len(years)} total)\n")

    for i, year in enumerate(years, 1):
        out_path = os.path.join(args.output_dir, f"{year}.db")
        if os.path.exists(out_path) and not args.force:
            print(f"[{i:4d}/{len(years)}] {year}  skip (exists)")
            continue

        print(f"[{i:4d}/{len(years)}] {year}  writing...", end="", flush=True)
        obs_count = split_year(args.db, year, out_path)

        if obs_count == 0:
            os.remove(out_path)
            print("  0 obs — removed")
        else:
            size_mb = os.path.getsize(out_path) / 1_048_576
            print(f"  {obs_count:>12,} obs  {size_mb:7.1f} MB")

    print("\nDone.")


if __name__ == "__main__":
    main()
