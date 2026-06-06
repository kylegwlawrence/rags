#!/usr/bin/env python3
"""Index data/ecfr/ecfr.db into data/ecfr/ecfr_rag.db.

Each regulation section is passed through `rag.ecfr.build_doc` (title +
flat prose body), then `rag.chunker.chunk_doc` splits it under the DENSE
profile (1000/1200/100 — short regulatory paragraphs).

Re-runnable via the shared `rag.indexer.run_indexer`. Version key is a
content hash of heading + content plus CLEANER_VERSION. After this script
runs, restart uvicorn so the cached connection picks up the new file.

Full corpus is ~509k chunks (~8 days on local Ollama). Use --limit to
index a subset, or run in the background with --title to scope to one
CFR title at a time (pass --title via add_extra_args if needed).
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import ecfr_rag_extract  # noqa: E402
from rag import profiles  # noqa: E402
from rag.cli import run_index_cli  # noqa: E402

ECFR_DB = REPO_ROOT / "data" / "ecfr" / "ecfr.db"
RAG_DB = REPO_ROOT / "data" / "ecfr" / "ecfr_rag.db"


if __name__ == "__main__":
    sys.exit(run_index_cli(
        description=__doc__,
        source_db_path=ECFR_DB,
        rag_db_path=RAG_DB,
        extractor_factory=lambda args: (
            lambda conn: ecfr_rag_extract.iter_docs(conn, limit=args.limit)
        ),
        chunker_defaults=profiles.DENSE,
        source_label="regulations",
    ))
