#!/usr/bin/env python3
"""Build an FTS5 index over bills.title + summary + subjects for free-text search.

Re-runnable: drops `bills_fts` and rebuilds from scratch. Run after every
billstatus download. Restart uvicorn after — the API caches the source
connection at import time.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag.fts import run_fts_indexer  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "billstatus" / "billstatus.db"


if __name__ == "__main__":
    sys.exit(run_fts_indexer(
        db_path=DB_PATH,
        virtual_table="bills_fts",
        content_table="bills",
        columns=("title", "summary", "subjects"),
        row_label="bills",
    ))
