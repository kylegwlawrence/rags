#!/usr/bin/env python3
"""Index data/arxiv/arxiv.db into data/arxiv/arxiv_rag.db.

Phase 2a embeds title + abstract only; full-HTML chunking is deferred to
Phase 3 where the OAI/render pipeline gets ported. Re-runnable via the shared
`rag.indexer.run_indexer`; skips papers whose `oai_datestamp` (or content-hash
fallback) matches the previously-stored `docs_meta.version`. Detects legacy
upstream schema (`paper_chunks*` from `local_wikipedia`) and rebuilds from
scratch. After this script runs, restart uvicorn so the cached connection
picks up the new file.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import arxiv_rag_extract  # noqa: E402
from rag import profiles  # noqa: E402
from rag.chunker import chunk_markdown  # noqa: E402
from rag.cli import run_index_cli  # noqa: E402

ARXIV_DB = REPO_ROOT / "data" / "arxiv" / "arxiv.db"
RAG_DB = REPO_ROOT / "data" / "arxiv" / "arxiv_rag.db"


if __name__ == "__main__":
    sys.exit(run_index_cli(
        description=__doc__,
        source_db_path=ARXIV_DB,
        rag_db_path=RAG_DB,
        extractor_factory=lambda args: (
            lambda conn: arxiv_rag_extract.iter_docs(conn, limit=args.limit)
        ),
        chunk_fn=chunk_markdown,
        chunker_defaults=profiles.DEFAULT,
        source_label="papers",
        legacy_table_prefixes=("paper_chunks",),
    ))
