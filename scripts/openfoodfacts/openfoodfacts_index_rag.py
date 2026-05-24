#!/usr/bin/env python3
"""Index data/openfoodfacts/openfoodfacts.db into data/openfoodfacts/openfoodfacts_rag.db.

Each product is rendered to section-headered markdown by
`openfoodfacts_rag_extract.iter_docs` (Product / Ingredients / Nutrition
sections), then `rag.chunker.chunk_markdown` splits on the `##` headings so
per-chunk `section` labels populate. Products with neither a name nor
ingredients text are skipped.

Re-runnable via the shared `rag.indexer.run_indexer`. Version key is a content
hash of the product's key fields plus CLEANER_VERSION. After this script runs,
restart uvicorn so the cached connection picks up the new file.

**Default `--limit 1000`** caps the run for safety — the full corpus is ~3 M
products and would take many hours on local Ollama; raise `--limit` explicitly
when ready.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import openfoodfacts_rag_extract  # noqa: E402
from rag import profiles  # noqa: E402
from rag.chunker import chunk_markdown  # noqa: E402
from rag.cli import run_index_cli  # noqa: E402

OPENFOODFACTS_DB = REPO_ROOT / "data" / "openfoodfacts" / "openfoodfacts.db"
RAG_DB = REPO_ROOT / "data" / "openfoodfacts" / "openfoodfacts_rag.db"


if __name__ == "__main__":
    sys.exit(run_index_cli(
        description=__doc__,
        source_db_path=OPENFOODFACTS_DB,
        rag_db_path=RAG_DB,
        extractor_factory=lambda args: (
            lambda conn: openfoodfacts_rag_extract.iter_docs(conn, limit=args.limit)
        ),
        chunk_fn=chunk_markdown,
        chunker_defaults=profiles.DENSE,
        limit_default=1000,
        limit_help="Process at most N products (default 1000, a safety cap).",
        source_label="products",
    ))
