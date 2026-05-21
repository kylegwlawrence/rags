#!/usr/bin/env python3
"""Build an FTS5 index over docs.title + docs.content for free-text search.

Creates `docs_fts` as an external-content FTS5 table backed by the existing
`docs` table: the index lives in `docs_fts`, but the original text stays in
`docs` (no duplication). Tokenizer is `porter unicode61` for stemming +
diacritic folding (matches arxiv_index_fts.py / openalex_index_fts.py).

Re-runnable: drops the virtual table and rebuilds from scratch. Run after
every refresh of python_docs.db.
"""

import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = REPO_ROOT / "data" / "pydocs" / "python_docs.db"


def main() -> int:
    if not DB_PATH.is_file():
        print(f"missing: {DB_PATH}", file=sys.stderr)
        return 1

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    t0 = time.time()
    cur.execute("DROP TABLE IF EXISTS docs_fts")
    cur.execute("""
        CREATE VIRTUAL TABLE docs_fts USING fts5(
            title,
            content,
            content='docs',
            content_rowid='id',
            tokenize='porter unicode61'
        )
    """)
    cur.execute(
        "INSERT INTO docs_fts(rowid, title, content) "
        "SELECT id, title, content FROM docs"
    )
    con.commit()

    indexed = cur.execute("SELECT COUNT(*) FROM docs_fts").fetchone()[0]
    db_size = DB_PATH.stat().st_size

    con.close()
    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s. "
          f"Indexed {indexed} docs. "
          f"DB file is now {db_size / (1024**2):.1f} MB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
