#!/usr/bin/env python3
"""Index data/wikihow/wikihow.db into data/wikihow/wikihow_rag.db.

Each wikiHow guide (a group of `articles` step rows sharing a title) is
rendered to section-headered markdown by `wikihow_rag_extract.iter_docs`, then
`rag.chunker.chunk_markdown` splits each guide on its `##` section headings so
per-chunk `section` labels (Overview / "Using Home Remedies" / ...) populate.

Re-runnable via the shared `rag.indexer.run_indexer`. Version key is a content
hash of the guide's steps plus CLEANER_VERSION — wikihow.db has no per-row
updated_at. After this script runs, restart uvicorn so the cached connection
picks up the new file.

**Default `--limit 100`** mirrors gutenberg's / simplewiki's safety default —
the full wikiHow corpus is large and would take many hours on local Ollama;
raise `--limit` explicitly (or pass a large value) when ready.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import wikihow_rag_extract  # noqa: E402
from rag import profiles  # noqa: E402
from rag.chunker import chunk_markdown  # noqa: E402
from rag.cli import run_index_cli  # noqa: E402

WIKIHOW_DB = REPO_ROOT / "data" / "wikihow" / "wikihow.db"
RAG_DB = REPO_ROOT / "data" / "wikihow" / "wikihow_rag.db"


if __name__ == "__main__":
    sys.exit(run_index_cli(
        description=__doc__,
        source_db_path=WIKIHOW_DB,
        rag_db_path=RAG_DB,
        extractor_factory=lambda args: (
            lambda conn: wikihow_rag_extract.iter_docs(conn, limit=args.limit)
        ),
        chunk_fn=chunk_markdown,
        chunker_defaults=profiles.DEFAULT,
        limit_default=100,
        limit_help="Process at most N guides (default 100, a safety cap).",
        source_label="guides",
    ))
