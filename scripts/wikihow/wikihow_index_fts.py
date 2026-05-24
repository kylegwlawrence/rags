#!/usr/bin/env python3
"""Build an FTS5 index over articles.title + headline + text for free-text search.

`articles.id` is an INTEGER PK alias for the rowid, so the FTS table uses it
as its content_rowid directly.

Re-runnable: drops `articles_fts` and rebuilds from scratch.
Restart uvicorn after — the API caches the source connection at import time.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag.fts import run_fts_indexer  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "wikihow" / "wikihow.db"


if __name__ == "__main__":
    sys.exit(run_fts_indexer(
        db_path=DB_PATH,
        virtual_table="articles_fts",
        content_table="articles",
        columns=("title", "headline", "text"),
        content_rowid="id",
        row_label="step rows",
    ))
