#!/usr/bin/env python3
"""Index data/sec_edgar/sec_edgar.db into data/sec_edgar/sec_edgar_rag.db.

Each fetched filing's body (from `sec_edgar_rag_extract.iter_docs`) is split by
`rag.chunker.chunk_doc` — filing text is flat prose with no reliable `##`
heading structure, so no markdown-aware splitting applies and every chunk
inherits the doc's (None) section.

Re-runnable via the shared `rag.indexer.run_indexer`. Version key is a content
hash of the body plus CLEANER_VERSION. After this script runs, restart uvicorn
so the cached connection picks up the new file.

Standalone: requires bodies already fetched by sec_edgar_fetch_bodies.py and a
running Ollama for embeddings.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import sec_edgar_rag_extract  # noqa: E402
from rag import profiles  # noqa: E402
from rag.cli import run_index_cli  # noqa: E402

SEC_EDGAR_DB = REPO_ROOT / "data" / "sec_edgar" / "sec_edgar.db"
RAG_DB = REPO_ROOT / "data" / "sec_edgar" / "sec_edgar_rag.db"


if __name__ == "__main__":
    sys.exit(run_index_cli(
        description=__doc__,
        source_db_path=SEC_EDGAR_DB,
        rag_db_path=RAG_DB,
        extractor_factory=lambda args: (
            lambda conn: sec_edgar_rag_extract.iter_docs(conn, limit=args.limit)
        ),
        chunker_defaults=profiles.DEFAULT,
        source_label="filings",
    ))
