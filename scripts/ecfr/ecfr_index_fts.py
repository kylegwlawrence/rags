#!/usr/bin/env python3
"""Build an FTS5 index over regulations.heading + content for free-text search.

Re-runnable: drops `regulations_fts` and rebuilds from scratch. Run after every
eCFR download. Restart uvicorn after — the API caches the source connection at
import time.

The backing `regulations` table uses an INTEGER PRIMARY KEY (`id`), which is the
table's rowid, so the FTS index is keyed on `id` directly.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag.fts import run_fts_indexer  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "ecfr" / "ecfr.db"


if __name__ == "__main__":
    sys.exit(run_fts_indexer(
        db_path=DB_PATH,
        virtual_table="regulations_fts",
        content_table="regulations",
        columns=("heading", "content"),
        content_rowid="id",
        row_label="regulations",
    ))
