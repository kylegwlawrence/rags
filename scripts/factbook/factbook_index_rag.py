#!/usr/bin/env python3
"""Index data/factbook/factbook.db into data/factbook/factbook_rag.db.

Each country's nested `data` JSON is rendered as markdown (one `##` heading
per top-level section), then chunked by `rag.chunker.chunk_markdown` so each
chunk carries its section name (Geography, Economy, etc.).

Re-runnable via the shared `rag.indexer.run_indexer`. Version key is a hash
of the raw JSON blob — change detection is all-or-nothing per country.
After this script runs, restart uvicorn so the cached connection picks up
the new file.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import factbook_rag_extract  # noqa: E402
from rag import profiles  # noqa: E402
from rag.chunker import chunk_markdown  # noqa: E402
from rag.cli import run_index_cli  # noqa: E402

FACTBOOK_DB = REPO_ROOT / "data" / "factbook" / "factbook.db"
RAG_DB = REPO_ROOT / "data" / "factbook" / "factbook_rag.db"


if __name__ == "__main__":
    sys.exit(run_index_cli(
        description=__doc__,
        source_db_path=FACTBOOK_DB,
        rag_db_path=RAG_DB,
        extractor_factory=lambda args: (
            lambda conn: factbook_rag_extract.iter_docs(conn, limit=args.limit)
        ),
        chunk_fn=chunk_markdown,
        chunker_defaults=profiles.DENSE,
        source_label="countries",
    ))
