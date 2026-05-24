#!/usr/bin/env python3
"""Build an FTS5 index over filings.company_name + body for free-text search.

Only rows with `status = 'fetched'` AND `body IS NOT NULL` are indexed —
metadata-only filings have no body to search. Re-runnable: drops `filings_fts`
and rebuilds from scratch. Run after every body-fetch run.
Restart uvicorn after — the API caches the source connection at import time.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag.fts import run_fts_indexer  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "sec_edgar" / "sec_edgar.db"


if __name__ == "__main__":
    sys.exit(run_fts_indexer(
        db_path=DB_PATH,
        virtual_table="filings_fts",
        content_table="filings",
        columns=("company_name", "body"),
        where="status = 'fetched' AND body IS NOT NULL",
        row_label="filings",
    ))
