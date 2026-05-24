#!/usr/bin/env python3
"""Build an FTS5 index over readmes.name + readme for free-text search.

Only rows with `status = 'fetched'` AND `readme IS NOT NULL` are indexed.
Re-runnable: drops `readmes_fts` and rebuilds from scratch.
Restart uvicorn after — the API caches the source connection at import time.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag.fts import run_fts_indexer  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "github" / "readmes.db"


if __name__ == "__main__":
    sys.exit(run_fts_indexer(
        db_path=DB_PATH,
        virtual_table="readmes_fts",
        content_table="readmes",
        columns=("name", "readme"),
        where="status = 'fetched' AND readme IS NOT NULL",
        row_label="READMEs",
    ))
