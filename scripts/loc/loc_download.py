#!/usr/bin/env python3
"""Download LOC items via the loc.gov search API into SQLite.

Filter by --format to target a specific original-format type.  The default
is "manuscript/mixed material" (~320 k English items, ~495 k all languages).
Pass --language "" to skip the language filter.

Writes to data/loc/loc_<format_slug>.db (override with --db).
Resumes from the last completed page if interrupted.

Valid --format values (approx 2026 counts, all languages):
  newspaper                 3,215,144
  photo, print, drawing     1,218,799
  book                        770,054
  periodical                  529,416
  manuscript/mixed material   495,134
  legislation                 458,500
  notated music               141,598
  film, video                  78,442
  sound recording              67,516
  map                          59,953
  web page                    105,493
  personal narrative           97,965

Omit --format entirely to download all formats (7 M+ items).
"""

import argparse
import os
import re
import sqlite3
import time
import urllib.parse
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_FORMAT = "manuscript/mixed material"
DEFAULT_LANGUAGE = "english"
BASE_URL = "https://www.loc.gov/search/"
PER_PAGE = 100
MAX_RETRIES = 3
REQUEST_DELAY = 3  # seconds between pages; LOC search API is lenient but be polite
_EMAIL = os.environ.get("DATASETS_EMAIL")
USER_AGENT = f"datasets-bot/1.0 (mailto:{_EMAIL})"


def _slugify(text: str) -> str:
    """Convert a format string to a safe filename component."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def default_db(fmt: str) -> str:
    slug = _slugify(fmt) if fmt else "all"
    return f"./data/loc/loc_{slug}.db"


def create_schema(cur: sqlite3.Cursor) -> None:
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id     TEXT UNIQUE,
            title       TEXT,
            date        TEXT,
            format      TEXT,
            creator     TEXT,
            subject     TEXT,
            description TEXT,
            language    TEXT,
            collection  TEXT,
            url         TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_items_format ON items (format);
        CREATE TABLE IF NOT EXISTS ingest_state (
            id                  INTEGER PRIMARY KEY CHECK (id = 1),
            last_completed_page INTEGER
        );
        INSERT OR IGNORE INTO ingest_state (id, last_completed_page) VALUES (1, NULL);
    """)


def get_last_completed_page(cur: sqlite3.Cursor) -> Optional[int]:
    row = cur.execute(
        "SELECT last_completed_page FROM ingest_state WHERE id = 1"
    ).fetchone()
    return row[0] if row else None


def build_fa(fmt: str, language: str) -> Optional[str]:
    """Build the pipe-separated LOC API fa (facet) filter string."""
    parts = []
    if fmt:
        parts.append(f"original-format:{fmt}")
    if language:
        parts.append(f"language:{language}")
    return "|".join(parts) if parts else None


def _encode_fa(fa: str) -> str:
    """Encode a LOC fa filter value, keeping colons literal (as the API requires).

    requests encodes ':' as '%3A' which breaks the LOC facet syntax
    (e.g. 'original-format:book'), so we build the fa portion of the
    query string manually and append it after letting requests encode
    the rest of the params normally.
    """
    # quote encodes everything except colons; swap %20 for + (LOC accepts both
    # but + matches the canonical form seen in browser requests).
    return urllib.parse.quote(fa, safe=":").replace("%20", "+")


def fetch_page(session: requests.Session, page: int, fa: Optional[str]) -> dict:
    """Fetch one page from the LOC search API, sleeping on 429."""
    params: dict = {
        "fo": "json",
        "c": PER_PAGE,
        "sp": page,
        "at": "results,pagination",
    }
    url = BASE_URL + "?" + urllib.parse.urlencode(params)
    if fa:
        url += "&fa=" + _encode_fa(fa)

    while True:
        resp = session.get(url, timeout=60, allow_redirects=True)
        if resp.status_code == 429:
            print("Rate limited — sleeping 60 s")
            time.sleep(60)
            continue
        resp.raise_for_status()
        return resp.json()


def parse_item(item: dict, fallback_fmt: str) -> tuple:
    """Extract and normalise fields from a single LOC search result."""
    item_id  = item.get("id", "")
    title    = item.get("title", "")
    url      = item.get("url", "")
    date     = item.get("date", "") or ""
    language = ", ".join(item.get("language", []))

    creators = item.get("contributor", []) or item.get("creator", [])
    creator  = "; ".join(creators) if isinstance(creators, list) else str(creators or "")

    subjects = item.get("subject", [])
    subject  = "; ".join(subjects) if isinstance(subjects, list) else str(subjects or "")

    desc = item.get("description", "") or item.get("summary", "") or ""
    if isinstance(desc, list):
        desc = " ".join(desc)

    partof     = item.get("partof", [])
    first      = partof[0] if partof else None
    collection = first.get("title", "") if isinstance(first, dict) else ""

    formats    = item.get("original_format", [])
    item_fmt   = formats[0] if formats else fallback_fmt

    return (item_id, title, date, item_fmt, creator, subject, desc, language, collection, url)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download LOC items via the search API into SQLite.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--format", default=DEFAULT_FORMAT,
        metavar="FORMAT",
        help=f'LOC original-format filter (default: "{DEFAULT_FORMAT}"). '
             'Pass "" to download all formats.',
    )
    parser.add_argument(
        "--language", default=DEFAULT_LANGUAGE,
        help=f'Language filter (default: "{DEFAULT_LANGUAGE}"). '
             'Pass "" to include all languages.',
    )
    parser.add_argument(
        "--db", default=None,
        help="Path to SQLite database (default: data/loc/loc_<format_slug>.db)",
    )
    args = parser.parse_args()

    if not _EMAIL:
        parser.error("DATASETS_EMAIL env var is required for the User-Agent contact address.")

    db_path = args.db or default_db(args.format)
    db_dir  = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    con = sqlite3.connect(db_path)
    cur = con.cursor()
    create_schema(cur)
    con.commit()

    last       = get_last_completed_page(cur)
    start_page = (last + 1) if last is not None else 1
    if last is not None:
        print(f"Resuming from page {start_page} (last completed: {last})")

    fa      = build_fa(args.format, args.language)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    total_inserted = 0
    page           = start_page

    fmt_label = args.format or "(all formats)"
    lang_label = args.language or "(all languages)"
    print(f"Starting LOC download — format: {fmt_label!r}  language: {lang_label!r}")
    print(f"Output: {db_path}")

    while True:
        print(f"Fetching page {page}...")

        data = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                data = fetch_page(session, page, fa)
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
            cur.execute(
                """
                INSERT OR IGNORE INTO items
                    (item_id, title, date, format, creator, subject,
                     description, language, collection, url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                parse_item(item, args.format),
            )
            total_inserted += cur.rowcount

        con.commit()
        cur.execute(
            "UPDATE ingest_state SET last_completed_page = ? WHERE id = 1", (page,)
        )
        con.commit()

        # LOC's pagination: "total" is the page count, "of" is the item count.
        pagination  = data.get("pagination", {})
        total_pages = pagination.get("total") or page
        total_items = pagination.get("of", 0)
        print(f"  Page {page}/{total_pages} ({total_items} items) — "
              f"inserted this run: {total_inserted}")

        if page >= total_pages:
            break
        page += 1
        time.sleep(REQUEST_DELAY)

    con.close()
    print(f"\nDone. Total records inserted: {total_inserted}")


if __name__ == "__main__":
    main()
