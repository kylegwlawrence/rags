#!/usr/bin/env python3
"""Build an FTS5 index over articles.title + headline + text for free-text search.

Creates `articles_fts` as an external-content FTS5 table backed by the
existing `articles` table: the index lives in `articles_fts`, but the
original text stays in `articles` (no duplication). Tokenizer is
`porter unicode61` for stemming + diacritic folding (matches
arxiv_index_fts.py / openalex_index_fts.py / python_docs_index_fts.py).

Re-runnable: drops the virtual table and rebuilds from scratch. Run after
every refresh of wikihow.db.
"""

import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = REPO_ROOT / "data" / "wikihow" / "wikihow.db"


def main() -> int:
    if not DB_PATH.is_file():
        print(f"missing: {DB_PATH}", file=sys.stderr)
        return 1

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    t0 = time.time()
    cur.execute("DROP TABLE IF EXISTS articles_fts")
    cur.execute("""
        CREATE VIRTUAL TABLE articles_fts USING fts5(
            title,
            headline,
            text,
            content='articles',
            content_rowid='id',
            tokenize='porter unicode61'
        )
    """)
    cur.execute(
        "INSERT INTO articles_fts(rowid, title, headline, text) "
        "SELECT id, title, headline, text FROM articles"
    )
    con.commit()

    indexed = cur.execute("SELECT COUNT(*) FROM articles_fts").fetchone()[0]
    db_size = DB_PATH.stat().st_size

    con.close()
    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s. "
          f"Indexed {indexed} step rows. "
          f"DB file is now {db_size / (1024**2):.1f} MB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
