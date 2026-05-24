#!/usr/bin/env python3
"""Index data/pydocs/python_docs.db into data/pydocs/python_docs_rag.db.

Each documentation page's Sphinx text-builder body is rendered to markdown
by `python_docs_rag_extract.sphinx_text_to_markdown` so the heading underlines
(===, ---, ~~~, ^^^, \"\"\") become `##` / `###` / `####` / `#####` / `######`,
and then `rag.chunker.chunk_markdown` splits each page on `##` / `###` / `####`
boundaries so per-chunk `section` labels (e.g. "Process Parameters",
"Built-in Constants") populate.

Re-runnable via the shared `rag.indexer.run_indexer`. Version key is
`sha256(content)[:32]-CLEANER_VERSION` — the source DB has no per-row
updated_at, so a content hash is the only edit-detection signal. After this
script runs, restart uvicorn so the cached connection picks up the new file.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import python_docs_rag_extract  # noqa: E402
from rag import profiles  # noqa: E402
from rag.chunker import chunk_markdown  # noqa: E402
from rag.cli import run_index_cli  # noqa: E402

PYDOCS_DB = REPO_ROOT / "data" / "pydocs" / "python_docs.db"
RAG_DB = REPO_ROOT / "data" / "pydocs" / "python_docs_rag.db"


if __name__ == "__main__":
    sys.exit(run_index_cli(
        description=__doc__,
        source_db_path=PYDOCS_DB,
        rag_db_path=RAG_DB,
        extractor_factory=lambda args: (
            lambda conn: python_docs_rag_extract.iter_docs(conn, limit=args.limit)
        ),
        chunk_fn=chunk_markdown,
        chunker_defaults=profiles.DEFAULT,
        source_label="docs",
    ))
