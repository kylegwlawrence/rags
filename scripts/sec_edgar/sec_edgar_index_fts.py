#!/usr/bin/env python3
"""Build an FTS5 index over filings.company_name + body for free-text search.

Creates `filings_fts` as an external-content FTS5 table backed by the
`filings` table. Only rows with `status = 'fetched'` are indexed (others have
no body text — fetch them first with sec_edgar_fetch_bodies.py). Because
`accession_number` is a TEXT primary key (not an INTEGER alias for the rowid),
`content_rowid` references the implicit SQLite rowid. Tokenizer is
`porter unicode61` for stemming + diacritic folding.

Re-runnable: drops the virtual table and rebuilds from scratch. Run after
every body-fetch run.
"""

import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = REPO_ROOT / "data" / "sec_edgar" / "sec_edgar.db"


def main() -> int:
    if not DB_PATH.is_file():
        print(f"missing: {DB_PATH}", file=sys.stderr)
        return 1

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    t0 = time.time()
    cur.execute("DROP TABLE IF EXISTS filings_fts")
    cur.execute("""
        CREATE VIRTUAL TABLE filings_fts USING fts5(
            company_name,
            body,
            content='filings',
            content_rowid='rowid',
            tokenize='porter unicode61'
        )
    """)
    cur.execute(
        "INSERT INTO filings_fts(rowid, company_name, body) "
        "SELECT rowid, company_name, body FROM filings "
        "WHERE status = 'fetched' AND body IS NOT NULL"
    )
    con.commit()

    # COUNT(*) on an external-content FTS5 table reports the *content* table's
    # row count (all filings), not how many were indexed — so count the rows we
    # actually inserted.
    indexed = cur.execute(
        "SELECT COUNT(*) FROM filings WHERE status = 'fetched' AND body IS NOT NULL"
    ).fetchone()[0]
    db_size = DB_PATH.stat().st_size

    con.close()
    elapsed = time.time() - t0
    print(
        f"Done in {elapsed:.1f}s. "
        f"Indexed {indexed} filings. "
        f"DB file is now {db_size / (1024**2):.1f} MB."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
