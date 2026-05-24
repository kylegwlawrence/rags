#!/usr/bin/env python3
"""Index data/federal_register/federal_register.db into data/federal_register/federal_register_rag.db.

Each document is rendered to section-headered markdown by
`federal_register_rag_extract.iter_docs` (Details / Abstract / Action /
Excerpts sections), then `rag.chunker.chunk_markdown` splits on the `##`
headings so per-chunk `section` labels populate.

Re-runnable via the shared `rag.indexer.run_indexer`. Version key is a content
hash of title + abstract + action + excerpts plus CLEANER_VERSION. After this
script runs, restart uvicorn so the cached connection picks up the new file.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import federal_register_rag_extract  # noqa: E402
from rag import profiles  # noqa: E402
from rag.chunker import chunk_markdown  # noqa: E402
from rag.cli import run_index_cli  # noqa: E402

FEDERAL_REGISTER_DB = REPO_ROOT / "data" / "federal_register" / "federal_register.db"
RAG_DB = REPO_ROOT / "data" / "federal_register" / "federal_register_rag.db"


if __name__ == "__main__":
    sys.exit(run_index_cli(
        description=__doc__,
        source_db_path=FEDERAL_REGISTER_DB,
        rag_db_path=RAG_DB,
        extractor_factory=lambda args: (
            lambda conn: federal_register_rag_extract.iter_docs(conn, limit=args.limit)
        ),
        chunk_fn=chunk_markdown,
        chunker_defaults=profiles.DEFAULT,
        source_label="documents",
    ))
