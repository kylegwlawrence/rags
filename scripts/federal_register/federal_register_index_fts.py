#!/usr/bin/env python3
"""Build an FTS5 index over documents.title + abstract for free-text search.

Creates `documents_fts` as an external-content FTS5 table backed by the
`documents` table. Because `document_number` is a TEXT primary key (not an
INTEGER alias for the rowid), `content_rowid` references the implicit SQLite
rowid. Tokenizer is `porter unicode61` for stemming + diacritic folding
(matches arxiv_index_fts.py / openalex_index_fts.py / python_docs_index_fts.py).

Re-runnable: drops the virtual table and rebuilds from scratch. Run after
every refresh of federal_register.db.
"""

import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = REPO_ROOT / "data" / "federal_register" / "federal_register.db"


def main() -> int:
    if not DB_PATH.is_file():
        print(f"missing: {DB_PATH}", file=sys.stderr)
        return 1

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    t0 = time.time()
    cur.execute("DROP TABLE IF EXISTS documents_fts")
    cur.execute("""
        CREATE VIRTUAL TABLE documents_fts USING fts5(
            title,
            abstract,
            content='documents',
            content_rowid='rowid',
            tokenize='porter unicode61'
        )
    """)
    cur.execute(
        "INSERT INTO documents_fts(rowid, title, abstract) "
        "SELECT rowid, title, abstract FROM documents"
    )
    con.commit()

    indexed = cur.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0]
    db_size = DB_PATH.stat().st_size

    con.close()
    elapsed = time.time() - t0
    print(
        f"Done in {elapsed:.1f}s. "
        f"Indexed {indexed} documents. "
        f"DB file is now {db_size / (1024**2):.1f} MB."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
