#!/usr/bin/env python3
"""Index data/sec_edgar/sec_edgar.db into data/sec_edgar/sec_edgar_rag.db.

Each fetched filing's body (from `sec_edgar_rag_extract.iter_docs`) is split by
`rag.chunker.chunk_doc` — filing text is flat prose with no reliable `##`
heading structure, so no markdown-aware splitting applies and every chunk
inherits the doc's (None) section.

Re-runnable via the shared `rag.indexer.run_indexer`. Version key is a content
hash of the body plus CLEANER_VERSION. After this script runs, restart uvicorn
so the cached connection picks up the new file.

Standalone: requires bodies already fetched by sec_edgar_fetch_bodies.py and a
running Ollama for embeddings.
"""

import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import sec_edgar_rag_extract  # noqa: E402
from rag import embedder  # noqa: E402
from rag.chunker import chunk_doc  # noqa: E402
from rag.indexer import run_indexer  # noqa: E402

SEC_EDGAR_DB = REPO_ROOT / "data" / "sec_edgar" / "sec_edgar.db"
RAG_DB = REPO_ROOT / "data" / "sec_edgar" / "sec_edgar_rag.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N filings (default: all fetched).")
    parser.add_argument("--reset", action="store_true",
                        help="Wipe sec_edgar_rag.db and rebuild from scratch.")
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
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be a positive integer when given")

    return run_indexer(
        source_db_path=SEC_EDGAR_DB,
        rag_db_path=RAG_DB,
        extractor=lambda conn: sec_edgar_rag_extract.iter_docs(conn, limit=args.limit),
        chunk_fn=chunk_doc,
        reset=args.reset,
        batch=args.batch,
        ollama_url=args.ollama_url,
        chunk_size=args.chunk_size,
        chunk_overlap=args.overlap,
        max_chunk_size=args.max_chunk_size,
        source_label="filings",
    )


if __name__ == "__main__":
    sys.exit(main())
