#!/usr/bin/env python3
"""Index data/openstax/openstax.db into data/openstax/openstax_rag.db.

Embeds one Doc per section (chapter section), chunked with the flat-prose
`chunk_doc` under the DEFAULT profile — each section is already one logical
unit, and its "Chapter — Section" label rides onto every chunk (and the embed
header) so a search hit names where it came from. Per-section Doc construction
lives in `rag.openstax.build_doc`; the indexer entry point is
`openstax_rag_extract.iter_docs`.

Re-runnable via the shared `rag.indexer.run_indexer`; skips sections whose
content-hash `version` matches the previously-stored value. After this script
runs, restart uvicorn so the cached connection picks up the new file.

The math corpus is a few thousand sections — a full pass is a couple of hours
on local Ollama (~1.4 s/chunk). Pass `--limit N` to cap a single run, or embed
sections on demand via `POST /openstax/sections/{book_id}/{module_id}/embed`.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import openstax_rag_extract  # noqa: E402
from rag import profiles  # noqa: E402
from rag.cli import run_index_cli  # noqa: E402

OPENSTAX_DB = REPO_ROOT / "data" / "openstax" / "openstax.db"
RAG_DB = REPO_ROOT / "data" / "openstax" / "openstax_rag.db"


if __name__ == "__main__":
    sys.exit(run_index_cli(
        description=__doc__,
        source_db_path=OPENSTAX_DB,
        rag_db_path=RAG_DB,
        extractor_factory=lambda args: (
            lambda conn: openstax_rag_extract.iter_docs(conn, limit=args.limit)
        ),
        chunker_defaults=profiles.DEFAULT,
        source_label="sections",
    ))
