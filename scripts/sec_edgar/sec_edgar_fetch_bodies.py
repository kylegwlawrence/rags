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
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag.sec_filing import (  # noqa: E402
    DEFAULT_DELAY,
    build_session,
    extract_primary_document,
    fetch_submission,
)

DEFAULT_DB = "data/sec_edgar/sec_edgar.db"


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch SEC EDGAR filing bodies into sec_edgar.db (standalone; "
                    "does not build FTS or RAG indexes)."
    )
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"SQLite database path (default: {DEFAULT_DB})")
    parser.add_argument("--accession",
                        help="Fetch a single filing by accession number (e.g. "
                             "0000912057-94-000263), ignoring --form-type / --limit / "
                             "status. Always refetches, even if already fetched.")
    parser.add_argument("--form-type", default="10-K",
                        help="Only fetch filings of this form type (default: 10-K). "
                             "Ignored when --accession is given.")
    parser.add_argument("--limit", type=int, default=200,
                        help="Max number of filings to fetch this run (default: 200). "
                             "Rows that already have a status are skipped.")
    parser.add_argument("--email",
                        default=os.environ.get("SEC_EMAIL"),
                        help="Contact email for SEC User-Agent header. Required (or set SEC_EMAIL env var).")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help=f"Seconds between requests (default: {DEFAULT_DELAY})")
    parser.add_argument("--reset-status", action="store_true",
                        help="Clear status/body for the chosen form type before fetching, "
                             "forcing a refetch.")
    args = parser.parse_args()

    if args.limit < 1:
        parser.error("--limit must be a positive integer")
    if not args.email:
        parser.error("--email is required (or set SEC_EMAIL env var)")

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"missing: {db_path} (run sec_edgar_download.py first)", file=sys.stderr)
        return 1

    con = sqlite3.connect(db_path)
    cur = con.cursor()
    ensure_columns(cur)
    con.commit()

    if args.accession:
        # Targeted single fetch: select by unique key, ignore status so an
        # already-fetched row is refetched on demand.
        rows = cur.execute(
            "SELECT accession_number, form_type, filing_url FROM filings "
            "WHERE accession_number = ?",
            (args.accession,),
        ).fetchall()
        if not rows:
            print(f"No filing with accession number {args.accession} found "
                  "(run sec_edgar_download.py to harvest its metadata first).",
                  file=sys.stderr)
            con.close()
            return 1
    else:
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

    session = build_session(args.email)

    fetched = missing = errored = 0
    for i, (accession_number, form_type, filing_url) in enumerate(rows, 1):
        text = fetch_submission(session, filing_url)
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
