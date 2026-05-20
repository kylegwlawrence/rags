#!/usr/bin/env python3
"""Build an FTS5 index over works.title + works.abstract for free-text search.

Creates `works_fts` as an external-content FTS5 table backed by the existing
`works` table: the index itself lives in `works_fts`, but the original text
stays in `works` (no duplication). Tokenizer is `porter unicode61` for stemming
+ diacritic folding.

Re-runnable: drops the virtual table and rebuilds from scratch. Run after
`openalex_download.py` whenever new works are added.
"""

import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = REPO_ROOT / "data" / "openalex" / "openalex.db"


def main() -> int:
    if not DB_PATH.is_file():
        print(f"missing: {DB_PATH}", file=sys.stderr)
        return 1

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    t0 = time.time()
    cur.execute("DROP TABLE IF EXISTS works_fts")
    cur.execute("""
        CREATE VIRTUAL TABLE works_fts USING fts5(
            title,
            abstract,
            content='works',
            content_rowid='rowid',
            tokenize='porter unicode61'
        )
    """)
    cur.execute(
        "INSERT INTO works_fts(rowid, title, abstract) "
        "SELECT rowid, title, abstract FROM works"
    )
    con.commit()

    indexed = cur.execute("SELECT COUNT(*) FROM works_fts").fetchone()[0]
    db_size = DB_PATH.stat().st_size

    con.close()
    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s. "
          f"Indexed {indexed} works. "
          f"DB file is now {db_size / (1024**2):.1f} MB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
