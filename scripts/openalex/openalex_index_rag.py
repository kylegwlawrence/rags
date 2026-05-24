#!/usr/bin/env python3
"""Index data/openalex/openalex.db into data/openalex/openalex_rag.db.

Samples the top-N most-cited works (default 5000) by `cited_by_count`.
Embedding the full 268k corpus is deferred — see
docs/retros/2026-05-18-openalex-phase-2b-rag.md for the scope decision.

Re-runnable via the shared `rag.indexer.run_indexer`; skips docs whose
content-hash `version` matches the previously-stored value. After this
script runs, restart uvicorn so the cached connection picks up the new file.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import openalex_rag_extract  # noqa: E402
from rag import profiles  # noqa: E402
from rag.cli import run_index_cli  # noqa: E402

OPENALEX_DB = REPO_ROOT / "data" / "openalex" / "openalex.db"
RAG_DB = REPO_ROOT / "data" / "openalex" / "openalex_rag.db"


if __name__ == "__main__":
    sys.exit(run_index_cli(
        description=__doc__,
        source_db_path=OPENALEX_DB,
        rag_db_path=RAG_DB,
        extractor_factory=lambda args: (
            lambda conn: openalex_rag_extract.iter_docs(conn, limit=args.limit)
        ),
        chunker_defaults=profiles.DEFAULT,
        limit_default=openalex_rag_extract.DEFAULT_LIMIT,
        limit_help=f"Process top-N works (default {openalex_rag_extract.DEFAULT_LIMIT}).",
        source_label="works",
        extra_meta_factory=lambda args: {"source_limit": str(args.limit)},
    ))
