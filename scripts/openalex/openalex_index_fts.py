#!/usr/bin/env python3
"""Build an FTS5 index over works.title + works.abstract for free-text search.

Re-runnable: drops `works_fts` and rebuilds from scratch.
Restart uvicorn after — the API caches the source connection at import time.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag.fts import run_fts_indexer  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "openalex" / "openalex.db"


if __name__ == "__main__":
    sys.exit(run_fts_indexer(
        db_path=DB_PATH,
        virtual_table="works_fts",
        content_table="works",
        columns=("title", "abstract"),
        row_label="works",
    ))
