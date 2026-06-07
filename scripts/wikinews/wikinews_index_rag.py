#!/usr/bin/env python3
"""Index data/wikinews/wikinews.db into data/wikinews/wikinews_rag.db.

Each main-namespace article's raw wikitext is rendered to markdown by
``rag.wikitext.wikitext_to_markdown`` and chunked by
``rag.chunker.chunk_markdown``.

The corpus is ~22k articles (the full English Wikinews archive), so this
runs to completion in a reasonable time. Default ``--limit 100`` lets you
verify the pipeline before committing to the full run; pass
``--limit 0`` (or a large number) for the full corpus.

Chunker defaults come from ``rag.profiles.WIKINEWS``; the API's live-embed
button reads the same profile.

Re-runnable: articles whose ``revision_id-CLEANER_VERSION`` version key is
already stored get skipped. Restart uvicorn after this script runs.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import wikinews_rag_extract  # noqa: E402
from rag import profiles  # noqa: E402
from rag.chunker import chunk_markdown  # noqa: E402
from rag.cli import run_index_cli  # noqa: E402

WIKINEWS_DB = REPO_ROOT / "data" / "wikinews" / "wikinews.db"
RAG_DB = REPO_ROOT / "data" / "wikinews" / "wikinews_rag.db"


if __name__ == "__main__":
    sys.exit(run_index_cli(
        description=__doc__,
        source_db_path=WIKINEWS_DB,
        rag_db_path=RAG_DB,
        extractor_factory=lambda args: (
            lambda conn: wikinews_rag_extract.iter_docs(conn, limit=args.limit)
        ),
        chunk_fn=chunk_markdown,
        chunker_defaults=profiles.WIKINEWS,
        limit_default=100,
        limit_help="Process at most N articles (default 100; full archive is ~22k).",
        source_label="articles",
        extra_meta_factory=lambda args: {"source_limit": str(args.limit)},
    ))
