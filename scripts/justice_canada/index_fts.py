#!/usr/bin/env python3
"""Build FTS5 indexes over the Justice Canada acts and regulations.

The corpus lives in two tables, so this builds two external-content virtual
tables:
  - acts_fts        (short_title + long_title + running_head + body)
  - regulations_fts (short_title + long_title + enabling_authority + body)

Both are required for the `?q=` full-text search and `sort=relevance` on
`/justice_canada/laws`. Re-runnable: each table is dropped and rebuilt. Run
after parse.py. Restart uvicorn afterwards — the API caches the source
connection at import time.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag.fts import run_fts_indexer  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "justice_canada" / "justice_canada.db"


if __name__ == "__main__":
    acts_rc = run_fts_indexer(
        db_path=DB_PATH,
        virtual_table="acts_fts",
        content_table="acts",
        columns=("short_title", "long_title", "running_head", "body"),
        row_label="acts",
    )
    regs_rc = run_fts_indexer(
        db_path=DB_PATH,
        virtual_table="regulations_fts",
        content_table="regulations",
        columns=("short_title", "long_title", "enabling_authority", "body"),
        row_label="regulations",
    )
    sys.exit(acts_rc or regs_rc)
