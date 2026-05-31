#!/usr/bin/env python3
"""Index data/pdfs/pdfs.db into data/pdfs/pdfs_rag.db.

Embeds each ingested PDF page by page so a semantic-search hit carries the page
it came from (stored in each chunk's `section` as `"p. {n}"`), letting the
frontend deep-link the in-browser viewer to that page. Page-aware chunking lives
in `pdfs_rag_extract.chunk_pdf`; the per-PDF Doc builder is `iter_docs`.

Re-runnable via the shared `rag.indexer.run_indexer`; skips PDFs whose
content-hash `version` matches the previously-stored value. After this script
runs, restart uvicorn so the cached connection picks up the new file.

The PDF drop folder is small and bounded, so a full pass is cheap — pass
`--limit N` only to cap a single run.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import pdfs_rag_extract  # noqa: E402
from rag import profiles  # noqa: E402
from rag.cli import run_index_cli  # noqa: E402

PDFS_DB = REPO_ROOT / "data" / "pdfs" / "pdfs.db"
RAG_DB = REPO_ROOT / "data" / "pdfs" / "pdfs_rag.db"


if __name__ == "__main__":
    sys.exit(run_index_cli(
        description=__doc__,
        source_db_path=PDFS_DB,
        rag_db_path=RAG_DB,
        extractor_factory=lambda args: (
            lambda conn: pdfs_rag_extract.iter_docs(conn, limit=args.limit)
        ),
        chunk_fn=pdfs_rag_extract.chunk_pdf,
        chunker_defaults=profiles.DEFAULT,
        source_label="PDFs",
    ))
