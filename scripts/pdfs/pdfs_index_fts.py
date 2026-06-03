#!/usr/bin/env python3
"""Build an FTS5 index over pages.text for full-text search of ingested PDFs.

PDF body text lives in the `pages` table (one row per page), so the index is
built at the page level keyed on each page's implicit rowid. The API's
`/pdfs/documents?q=` endpoint joins page hits back up to their parent document
and de-duplicates, so search results stay document-level even though the index
is per-page.

Re-runnable: drops `pages_fts` and rebuilds from scratch. Run after every
`pdfs_ingest.py` run that adds or re-ingests PDFs. Restart uvicorn after — the
API caches the source connection at import time.
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag.fts import run_fts_indexer  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "pdfs" / "pdfs.db"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help=f"SQLite DB to index (default: {DB_PATH}). Pass a LOC-specific "
             "DB, e.g. data/loc/loc_pdfs.db, to index PDFs ingested there.",
    )
    args = parser.parse_args()
    sys.exit(run_fts_indexer(
        db_path=args.db,
        virtual_table="pages_fts",
        content_table="pages",
        columns=("text",),
        content_rowid="rowid",
        row_label="pages",
    ))
