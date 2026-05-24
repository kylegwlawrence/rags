#!/usr/bin/env python3
"""Index data/github/readmes.db into data/github/github_readmes_rag.db.

Each fetched README is passed directly to `rag.chunker.chunk_markdown`, which
splits on `##` headings so per-chunk `section` labels populate from the
README's own heading structure (Installation, Usage, Contributing, etc.).
READMEs that are empty or only whitespace are skipped.

Re-runnable via the shared `rag.indexer.run_indexer`. Version key is a content
hash of the readme text plus CLEANER_VERSION. After this script runs, restart
uvicorn so the cached connection picks up the new file.

**Default `--limit 100`** caps the run for safety — the full corpus can be
large; pass a large value (or omit) for the whole thing.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import github_readmes_rag_extract  # noqa: E402
from rag import profiles  # noqa: E402
from rag.chunker import chunk_markdown  # noqa: E402
from rag.cli import run_index_cli  # noqa: E402

GITHUB_DB = REPO_ROOT / "data" / "github" / "readmes.db"
RAG_DB = REPO_ROOT / "data" / "github" / "github_readmes_rag.db"


if __name__ == "__main__":
    sys.exit(run_index_cli(
        description=__doc__,
        source_db_path=GITHUB_DB,
        rag_db_path=RAG_DB,
        extractor_factory=lambda args: (
            lambda conn: github_readmes_rag_extract.iter_docs(conn, limit=args.limit)
        ),
        chunk_fn=chunk_markdown,
        chunker_defaults=profiles.DEFAULT,
        limit_default=100,
        limit_help="Process at most N READMEs (default 100, a safety cap).",
        source_label="READMEs",
    ))
