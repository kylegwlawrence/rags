#!/usr/bin/env python3
"""Index data/factbook/factbook.db into data/factbook/factbook_rag.db.

Each country's nested `data` JSON is rendered as markdown (one `##` heading
per top-level section), then chunked by `rag.chunker.chunk_markdown` so each
chunk carries its section name (Geography, Economy, etc.).

Re-runnable via the shared `rag.indexer.run_indexer`. Version key is a hash
of the raw JSON blob — change detection is all-or-nothing per country.
After this script runs, restart uvicorn so the cached connection picks up
the new file.
"""

import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import factbook_rag_extract  # noqa: E402
from rag import embedder  # noqa: E402
from rag.chunker import chunk_markdown  # noqa: E402
from rag.indexer import run_indexer  # noqa: E402

FACTBOOK_DB = REPO_ROOT / "data" / "factbook" / "factbook.db"
RAG_DB = REPO_ROOT / "data" / "factbook" / "factbook_rag.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N countries (testing).")
    parser.add_argument("--reset", action="store_true",
                        help="Wipe factbook_rag.db and rebuild from scratch.")
    parser.add_argument("--batch", type=int, default=32,
                        help="Embedding batch size (chunks per HTTP call).")
    parser.add_argument("--ollama-url", default=embedder.OLLAMA_URL,
                        help="Override Ollama base URL.")
    parser.add_argument("--chunk-size", type=int, default=1000,
                        help="Soft target chars per chunk (default 1000 — factbook is dense key:value).")
    parser.add_argument("--max-chunk-size", type=int, default=1200,
                        help="Hard cap on chunk length (default 1200).")
    parser.add_argument("--overlap", type=int, default=100,
                        help="Inter-chunk overlap in chars (default 100 = 10%% of chunk-size). "
                             "Overlap is within-section only — does not carry across ## heading boundaries.")
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
        source_db_path=FACTBOOK_DB,
        rag_db_path=RAG_DB,
        extractor=lambda conn: factbook_rag_extract.iter_docs(conn, limit=args.limit),
        chunk_fn=chunk_markdown,
        reset=args.reset,
        batch=args.batch,
        ollama_url=args.ollama_url,
        chunk_size=args.chunk_size,
        chunk_overlap=args.overlap,
        max_chunk_size=args.max_chunk_size,
        source_label="countries",
    )


if __name__ == "__main__":
    sys.exit(main())
