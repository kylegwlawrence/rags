#!/usr/bin/env python3
"""Index data/eurlex/eurlex.db into data/eurlex/eurlex_rag.db.

Embeds each law's full `act_raw_text` body as flat prose (no markdown
section structure — extracted text doesn't carry reliable `##` headings).
Re-runnable via the shared `rag.indexer.run_indexer`; skips docs whose
content-hash `version` matches the previously-stored value. After this
script runs, restart uvicorn so the cached connection picks up the new file.

Full eurlex corpus is ~142k laws — many hours on local Ollama. Pass
`--limit N` to cap a single run.
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import eurlex_rag_extract  # noqa: E402
from rag import profiles  # noqa: E402
from rag.cli import run_index_cli  # noqa: E402

EURLEX_DB = REPO_ROOT / "data" / "eurlex" / "eurlex.db"
RAG_DB = REPO_ROOT / "data" / "eurlex" / "eurlex_rag.db"


if __name__ == "__main__":
    sys.exit(run_index_cli(
        description=__doc__,
        source_db_path=EURLEX_DB,
        rag_db_path=RAG_DB,
        extractor_factory=lambda args: (
            lambda conn: eurlex_rag_extract.iter_docs(conn, limit=args.limit)
        ),
        chunker_defaults=profiles.DEFAULT,
        source_label="laws",
    ))
