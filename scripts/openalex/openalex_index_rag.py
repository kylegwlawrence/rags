#!/usr/bin/env python3
"""Index data/openalex/openalex.db into data/openalex/openalex_rag.db.

Samples the top-N most-cited works (default 5000) by `cited_by_count`.
Embedding the full 268k corpus is deferred — see
docs/retros/2026-05-18-openalex-phase-2b-rag.md for the scope decision.

Re-runnable via the shared `rag.indexer.run_indexer`; skips docs whose
content-hash `version` matches the previously-stored value. After this
script runs, restart uvicorn so the cached connection picks up the new file.
"""

import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import openalex_rag_extract  # noqa: E402
from rag import embedder  # noqa: E402
from rag.indexer import run_indexer  # noqa: E402

OPENALEX_DB = REPO_ROOT / "data" / "openalex" / "openalex.db"
RAG_DB = REPO_ROOT / "data" / "openalex" / "openalex_rag.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=openalex_rag_extract.DEFAULT_LIMIT,
                        help=f"Process top-N works (default {openalex_rag_extract.DEFAULT_LIMIT}).")
    parser.add_argument("--reset", action="store_true",
                        help="Wipe openalex_rag.db and rebuild from scratch.")
    parser.add_argument("--batch", type=int, default=32,
                        help="Embedding batch size (chunks per HTTP call).")
    parser.add_argument("--ollama-url", default=embedder.OLLAMA_URL,
                        help="Override Ollama base URL.")
    parser.add_argument("--chunk-size", type=int, default=1500,
                        help="Soft target chars per chunk (default 1500).")
    parser.add_argument("--max-chunk-size", type=int, default=1800,
                        help="Hard cap on chunk length (default 1800).")
    parser.add_argument("--overlap", type=int, default=150,
                        help="Inter-chunk overlap in chars (default 150 = 10%% of chunk-size).")
    args = parser.parse_args()

    if args.chunk_size < 1:
        parser.error("--chunk-size must be a positive integer")
    if args.max_chunk_size < args.chunk_size:
        parser.error("--max-chunk-size must be >= --chunk-size")
    if args.overlap < 0 or args.overlap >= args.chunk_size:
        parser.error("--overlap must be >= 0 and < --chunk-size")
    if args.batch < 1:
        parser.error("--batch must be a positive integer")
    if args.limit < 1:
        parser.error("--limit must be a positive integer")

    return run_indexer(
        source_db_path=OPENALEX_DB,
        rag_db_path=RAG_DB,
        extractor=lambda conn: openalex_rag_extract.iter_docs(conn, limit=args.limit),
        reset=args.reset,
        batch=args.batch,
        ollama_url=args.ollama_url,
        chunk_size=args.chunk_size,
        chunk_overlap=args.overlap,
        max_chunk_size=args.max_chunk_size,
        extra_meta={"source_limit": str(args.limit)},
        source_label="works",
    )


if __name__ == "__main__":
    sys.exit(main())
