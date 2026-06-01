#!/usr/bin/env python3
"""Build an FTS5 index over papers.title + papers.abstract for free-text search.

Re-runnable: drops `papers_fts` and rebuilds from scratch.
Restart uvicorn after — the API caches the source connection at import time.

Pass `--db` to index a per-category shard instead of the monolith, e.g.::

    python scripts/arxiv/arxiv_index_fts.py --db data/arxiv/math.db
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rag.fts import run_fts_indexer  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "arxiv" / "arxiv.db"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help="Database to build papers_fts in (default: data/arxiv/arxiv.db). "
        "Point at a per-category shard, e.g. data/arxiv/math.db.",
    )
    args = parser.parse_args()

    sys.exit(run_fts_indexer(
        db_path=args.db,
        virtual_table="papers_fts",
        content_table="papers",
        columns=("title", "abstract"),
        row_label="papers",
    ))
