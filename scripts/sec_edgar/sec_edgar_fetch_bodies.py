#!/usr/bin/env python3

"""Fetch SEC EDGAR filing bodies into the existing sec_edgar.db.

`sec_edgar_download.py` records filing *metadata* only — one row per filing
with a `filing_url` pointing at SEC's full-submission `.txt` file. This script
downloads those bodies on demand and stores the extracted primary-document
text back on the `filings` row, so the FTS and RAG indexers have something to
index.

It is standalone: running it never triggers FTS or chunk/embed work. Build
those indexes afterwards with `sec_edgar_index_fts.py` / `sec_edgar_index_rag.py`.

The fetched/missing/error bookkeeping mirrors
`scripts/github_readmes/github_readmes_download.py`: a `status` column lets
re-runs resume without re-fetching rows that already have an outcome.

Defaults to 10-K filings, newest first, capped at 200 per run. Requires:
requests (and beautifulsoup4 via rag.cleaner.strip_html).
"""

import argparse
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag.cleaner import normalize_whitespace, strip_html  # noqa: E402

DEFAULT_DB = "data/sec_edgar/sec_edgar.db"
DELAY = 0.15   # SEC rate limit: 10 req/sec max; 0.15s gives headroom
MAX_RETRIES = 3

_DOCUMENT_RE = re.compile(r"<DOCUMENT>(.*?)</DOCUMENT>", re.DOTALL | re.IGNORECASE)
_TYPE_RE = re.compile(r"<TYPE>\s*([^\n<]+)", re.IGNORECASE)
_TEXT_RE = re.compile(r"<TEXT>(.*?)</TEXT>", re.DOTALL | re.IGNORECASE)
_HEADER_END_RE = re.compile(r"</(?:SEC|IMS)-HEADER>", re.IGNORECASE)


def ensure_columns(cur: sqlite3.Cursor) -> None:
    """Add the `body` / `status` columns and a status index if missing.

    Lets this script run against a sec_edgar.db produced by the metadata-only
    downloader without forcing a re-download.
    """
    cols = {row[1] for row in cur.execute("PRAGMA table_info(filings)")}
    if "body" not in cols:
        cur.execute("ALTER TABLE filings ADD COLUMN body TEXT")
    if "status" not in cols:
        cur.execute("ALTER TABLE filings ADD COLUMN status TEXT")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_status ON filings(status)")


def extract_primary_document(text: str, form_type: str) -> str:
    """Return the cleaned text of a filing's primary document.

    SEC full-submission `.txt` files are SGML: a `<SEC-HEADER>` block followed
    by one or more `<DOCUMENT>` blocks, each carrying a `<TYPE>` and a
    `<TEXT>` payload (HTML for modern filings, plain text for older ones).
    We pick the `<DOCUMENT>` whose `<TYPE>` matches the form (e.g. `10-K`),
    falling back to the first document, then to everything after the header
    for pre-`<DOCUMENT>` legacy filings. The chosen payload is HTML-stripped
    and whitespace-normalised.
    """
    blocks = _DOCUMENT_RE.findall(text)
    payload: Optional[str] = None

    if blocks:
        target = form_type.strip().upper()
        for block in blocks:
            type_match = _TYPE_RE.search(block)
            doc_type = type_match.group(1).strip().upper() if type_match else ""
            if doc_type == target:
                text_match = _TEXT_RE.search(block)
                payload = text_match.group(1) if text_match else block
                break
        if payload is None:
            # No type match — use the first document's TEXT payload.
            first = blocks[0]
            text_match = _TEXT_RE.search(first)
            payload = text_match.group(1) if text_match else first
    else:
        # Legacy filing with no <DOCUMENT> tags: take everything after the header.
        header_end = _HEADER_END_RE.search(text)
        payload = text[header_end.end():] if header_end else text

    return normalize_whitespace(strip_html(payload or ""))


def fetch_body(session: requests.Session, url: str) -> Optional[str]:
    """Fetch one filing's raw submission text, honouring SEC rate limits.

    Returns None on a 404 (treated as a permanent miss) or after exhausting
    retries on transient errors.
    """
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch SEC EDGAR filing bodies into sec_edgar.db (standalone; "
                    "does not build FTS or RAG indexes)."
    )
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"SQLite database path (default: {DEFAULT_DB})")
    parser.add_argument("--form-type", default="10-K",
                        help="Only fetch filings of this form type (default: 10-K)")
    parser.add_argument("--limit", type=int, default=200,
                        help="Max number of filings to fetch this run (default: 200). "
                             "Rows that already have a status are skipped.")
    parser.add_argument("--email",
                        default=os.environ.get("SEC_EMAIL", "kylegwlawrence@gmail.com"),
                        help="Contact email for SEC User-Agent header (or set SEC_EMAIL env var)")
    parser.add_argument("--delay", type=float, default=DELAY,
                        help=f"Seconds between requests (default: {DELAY})")
    parser.add_argument("--reset-status", action="store_true",
                        help="Clear status/body for the chosen form type before fetching, "
                             "forcing a refetch.")
    args = parser.parse_args()

    if args.limit < 1:
        parser.error("--limit must be a positive integer")

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"missing: {db_path} (run sec_edgar_download.py first)", file=sys.stderr)
        return 1

    con = sqlite3.connect(db_path)
    cur = con.cursor()
    ensure_columns(cur)
    con.commit()

    if args.reset_status:
        cur.execute(
            "UPDATE filings SET status = NULL, body = NULL WHERE form_type = ?",
            (args.form_type,),
        )
        con.commit()
        print(f"Cleared status/body for {cur.rowcount} {args.form_type} filings.")

    rows = cur.execute(
        "SELECT accession_number, form_type, filing_url FROM filings "
        "WHERE form_type = ? AND status IS NULL "
        "ORDER BY date_filed DESC LIMIT ?",
        (args.form_type, args.limit),
    ).fetchall()

    if not rows:
        print(f"No unfetched {args.form_type} filings found.")
        con.close()
        return 0

    session = requests.Session()
    session.headers.update({"User-Agent": f"sec-edgar-fetcher {args.email}"})

    fetched = missing = errored = 0
    for i, (accession_number, form_type, filing_url) in enumerate(rows, 1):
        text = fetch_body(session, filing_url)
        if text is None:
            cur.execute(
                "UPDATE filings SET status = 'error' WHERE accession_number = ?",
                (accession_number,),
            )
            errored += 1
        else:
            body = extract_primary_document(text, form_type)
            if body.strip():
                cur.execute(
                    "UPDATE filings SET body = ?, status = 'fetched' WHERE accession_number = ?",
                    (body, accession_number),
                )
                fetched += 1
            else:
                cur.execute(
                    "UPDATE filings SET status = 'missing' WHERE accession_number = ?",
                    (accession_number,),
                )
                missing += 1

        if i % 50 == 0:
            con.commit()
            print(f"  {i}/{len(rows)} — fetched {fetched}, missing {missing}, errored {errored}")

        time.sleep(args.delay)

    con.commit()
    con.close()
    print(f"\nDone. Fetched {fetched}, missing {missing}, errored {errored}.")
    print("Next: sec_edgar_index_fts.py and sec_edgar_index_rag.py to build the indexes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
