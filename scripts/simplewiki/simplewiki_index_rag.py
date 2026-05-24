#!/usr/bin/env python3
"""Index data/simplewiki/simplewiki.db into data/simplewiki/simplewiki_rag.db.

Each main-namespace article's raw wikitext is rendered to section-headered
markdown by `rag.wikitext.wikitext_to_markdown` and then chunked by
`rag.chunker.chunk_markdown` so each chunk carries its section heading
(e.g. "Geography", "History", "References") in the `chunks.section` column.

Default `--limit 100` mirrors gutenberg's safety-default — the full simplewiki
corpus is ~394k articles and would take many hours on local Ollama. Pass a
higher `--limit` (or remove it via `--limit 999999999`) once you are committed
to the runtime.

Chunker defaults come from `rag.profiles.SIMPLEWIKI`; the API's live-embed
button reads the same profile so a button-embed matches a batch-indexer run.

Re-runnable via the shared `rag.indexer.run_indexer`: docs whose
`revision_id-CLEANER_VERSION` version key matches the previously-stored
value get skipped. After this script runs, restart uvicorn so the cached
connection picks up the new file.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import simplewiki_rag_extract  # noqa: E402
from rag import profiles  # noqa: E402
from rag.chunker import chunk_markdown  # noqa: E402
from rag.cli import run_index_cli  # noqa: E402

SIMPLEWIKI_DB = REPO_ROOT / "data" / "simplewiki" / "simplewiki.db"
RAG_DB = REPO_ROOT / "data" / "simplewiki" / "simplewiki_rag.db"


if __name__ == "__main__":
    sys.exit(run_index_cli(
        description=__doc__,
        source_db_path=SIMPLEWIKI_DB,
        rag_db_path=RAG_DB,
        extractor_factory=lambda args: (
            lambda conn: simplewiki_rag_extract.iter_docs(conn, limit=args.limit)
        ),
        chunk_fn=chunk_markdown,
        chunker_defaults=profiles.SIMPLEWIKI,
        limit_default=100,
        limit_help="Process at most N articles (default 100; full corpus is ~394k).",
        source_label="articles",
        extra_meta_factory=lambda args: {"source_limit": str(args.limit)},
    ))
