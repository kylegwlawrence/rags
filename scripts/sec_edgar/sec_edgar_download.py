#!/usr/bin/env python3

"""
SEC EDGAR Filing Metadata Downloader
Downloads metadata for 10-K, 10-Q, and 8-K filings from SEC EDGAR
into SQLite via the quarterly full-index files. Covers 1993–present.
Stores filing URLs for on-demand full-text retrieval.
Requires: requests
"""

import argparse
import datetime
import os
import re
import sqlite3
import time
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://www.sec.gov/Archives/edgar/full-index"
FORM_TYPES = {"10-K", "10-Q", "8-K"}
DELAY = 0.15   # SEC rate limit: 10 req/sec max; 0.15s gives headroom
MAX_RETRIES = 3


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS filings (
            accession_number TEXT PRIMARY KEY,
            company_name     TEXT,
            cik              TEXT,
            form_type        TEXT,
            date_filed       TEXT,
            filename         TEXT,
            filing_url       TEXT,
            body             TEXT,
            status           TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_form   ON filings(form_type);
        CREATE INDEX IF NOT EXISTS idx_date   ON filings(date_filed);
        CREATE INDEX IF NOT EXISTS idx_cik    ON filings(cik);
        CREATE INDEX IF NOT EXISTS idx_status ON filings(status);
        CREATE TABLE IF NOT EXISTS ingest_state (
            id                  INTEGER PRIMARY KEY CHECK (id = 1),
            last_completed_year INTEGER,
            last_completed_qtr  INTEGER
        );
        INSERT OR IGNORE INTO ingest_state (id, last_completed_year, last_completed_qtr)
        VALUES (1, NULL, NULL);
    """)


def get_ingest_state(cur: sqlite3.Cursor) -> tuple[Optional[int], Optional[int]]:
    row = cur.execute(
        "SELECT last_completed_year, last_completed_qtr FROM ingest_state WHERE id = 1"
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def set_ingest_state(cur: sqlite3.Cursor, year: int, quarter: int) -> None:
    cur.execute(
        "UPDATE ingest_state SET last_completed_year = ?, last_completed_qtr = ? WHERE id = 1",
        (year, quarter),
    )


def fetch_index(session: requests.Session, year: int, quarter: int) -> Optional[str]:
    """Fetch the full-index company.idx file for a given year/quarter."""
    url = f"{BASE_URL}/{year}/QTR{quarter}/company.idx"
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(url, timeout=60)
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 60))
                print(f"  Rate limited — sleeping {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.text
        except requests.RequestException as exc:
            if attempt < MAX_RETRIES - 1:
                print(f"  Request error: {exc}, retrying...")
                time.sleep(5)
            else:
                print(f"  Failed after {MAX_RETRIES} attempts: {exc}")
    return None


# The header advertises fixed columns (form @62, cik @74, date @86, file @98)
# but the data rows don't actually align to those positions — the date and
# filename sit several columns to the right, so naive slicing splits the date
# (`2025-02-13` → `2025-02` + `-13`) and corrupts the filename. Parse by
# structure instead: form type has no spaces, CIK is digits, date is
# YYYY-MM-DD, and the filename is the trailing `edgar/...txt` path. The leading
# `.+?` soaks up company names that contain spaces.
_ROW_RE = re.compile(
    r"^(?P<company>.+?)\s+(?P<form>\S+)\s+(?P<cik>\d+)\s+"
    r"(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<filename>edgar/\S+\.txt)\s*$"
)


def parse_index(text: str) -> list[tuple]:
    """Parse a company.idx file into rows, keeping only the form types we want."""
    rows = []
    lines = text.splitlines()
    # First 9 lines are the EDGAR full-index header
    for line in lines[9:]:
        match = _ROW_RE.match(line)
        if match is None:
            continue
        form_type = match.group("form")
        if form_type not in FORM_TYPES:
            continue
        company_name = match.group("company").strip()
        cik          = match.group("cik")
        date_filed   = match.group("date")
        filename     = match.group("filename")

        # Canonical accession number: basename without extension
        # e.g. "edgar/data/1234567/0001234567-94-000001.txt" → "0001234567-94-000001"
        basename = filename.rsplit("/", 1)[-1]
        accession_number = basename.split(".")[0]

        filing_url = f"https://www.sec.gov/Archives/{filename}"
        rows.append((
            accession_number, company_name, cik,
            form_type, date_filed, filename, filing_url,
        ))
    return rows


def quarters_to_process(
    start_year: int,
    end_year: int,
    resume_year: Optional[int],
    resume_qtr: Optional[int],
) -> list[tuple[int, int]]:
    """Return (year, quarter) pairs to process, skipping already-completed ones."""
    all_quarters = [
        (y, q)
        for y in range(start_year, end_year + 1)
        for q in range(1, 5)
    ]
    if resume_year is None:
        return all_quarters
    # A NULL quarter would make the tuple comparison raise; treat it as the
    # start of that year (0 sorts before quarter 1).
    resume = (resume_year, resume_qtr if resume_qtr is not None else 0)
    return [(y, q) for y, q in all_quarters if (y, q) > resume]


def main() -> None:
    current_year = datetime.date.today().year
    parser = argparse.ArgumentParser(description="Download SEC EDGAR filing metadata into SQLite")
    parser.add_argument("--db", default="data/sec_edgar/sec_edgar.db")
    parser.add_argument("--start-year", type=int, default=1993,
                        help="First year to fetch (default: 1993)")
    parser.add_argument("--end-year", type=int, default=current_year,
                        help="Last year to fetch (default: current year)")
    parser.add_argument("--email",
                        default=os.environ.get("DATASETS_EMAIL"),
                        help="Contact email for SEC User-Agent header. Required (or set DATASETS_EMAIL env var).")
    parser.add_argument("--reset", action="store_true",
                        help="Drop and recreate tables before downloading")
    args = parser.parse_args()

    if not args.email:
        parser.error("--email is required (or set DATASETS_EMAIL env var)")

    os.makedirs(os.path.dirname(args.db), exist_ok=True)

    con = sqlite3.connect(args.db)
    cur = con.cursor()

    if args.reset:
        cur.executescript("""
            DROP TABLE IF EXISTS filings;
            DROP TABLE IF EXISTS ingest_state;
        """)
        con.commit()

    create_schema(cur)
    con.commit()

    resume_year, resume_qtr = get_ingest_state(cur)
    if resume_year:
        print(f"Resuming from after {resume_year} Q{resume_qtr}")

    session = requests.Session()
    session.headers.update({"User-Agent": f"sec-edgar-fetcher {args.email}"})

    quarters = quarters_to_process(args.start_year, args.end_year, resume_year, resume_qtr)
    total = 0

    for year, quarter in quarters:
        print(f"Fetching {year} Q{quarter}...")
        text = fetch_index(session, year, quarter)

        if text is None:
            print("  Not found — skipping")
            # Mark as completed so we don't retry on resume
            set_ingest_state(cur, year, quarter)
            con.commit()
            time.sleep(DELAY)
            continue

        rows = parse_index(text)
        if rows:
            cur.executemany("""
                INSERT OR IGNORE INTO filings
                (accession_number, company_name, cik, form_type, date_filed, filename, filing_url)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, rows)
            total += len(rows)
            print(f"  {len(rows)} filings inserted (total: {total})")
        else:
            print("  No matching filings")

        set_ingest_state(cur, year, quarter)
        con.commit()
        time.sleep(DELAY)

    con.close()
    print(f"\nDone. Total filings inserted: {total}")
    print(f"DB: {args.db}")
    print("Use filing_url to fetch full filing text on demand from SEC EDGAR.")


if __name__ == "__main__":
    main()
