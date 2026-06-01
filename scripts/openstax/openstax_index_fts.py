#!/usr/bin/env python3
"""Build an FTS5 index over sections.title + objectives + body.

Powers the `/openstax/sections?q=` full-text search. The backing `sections`
table uses an INTEGER PRIMARY KEY (`id`), which is the table's rowid, so the
FTS index is keyed on `id` directly.

Re-runnable: drops `sections_fts` and rebuilds from scratch. Run after every
`openstax_download.py` run. Restart uvicorn after — the API caches the source
connection at import time.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag.fts import run_fts_indexer  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "openstax" / "openstax.db"


if __name__ == "__main__":
    sys.exit(run_fts_indexer(
        db_path=DB_PATH,
        virtual_table="sections_fts",
        content_table="sections",
        columns=("title", "objectives", "body"),
        content_rowid="id",
        row_label="sections",
    ))
