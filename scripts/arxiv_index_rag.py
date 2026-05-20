#!/usr/bin/env python3
"""Index data/arxiv/arxiv.db into data/arxiv/arxiv_rag.db.

Phase 2a embeds title + abstract only; full-HTML chunking is deferred to
Phase 3 where the OAI/render pipeline gets ported. Re-runnable via the shared
`rag.indexer.run_indexer`; skips papers whose `oai_datestamp` (or content-hash
fallback) matches the previously-stored `docs_meta.version`. Detects legacy
upstream schema (`paper_chunks*` from `local_wikipedia`) and rebuilds from
scratch. After this script runs, restart uvicorn so the cached connection
picks up the new file.
"""

import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import arxiv_rag_extract  # noqa: E402
from rag import embedder  # noqa: E402
from rag.chunker import chunk_markdown  # noqa: E402
from rag.indexer import run_indexer  # noqa: E402

ARXIV_DB = REPO_ROOT / "data" / "arxiv" / "arxiv.db"
RAG_DB = REPO_ROOT / "data" / "arxiv" / "arxiv_rag.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N papers (testing).")
    parser.add_argument("--reset", action="store_true",
                        help="Wipe arxiv_rag.db and rebuild from scratch.")
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
        source_db_path=ARXIV_DB,
        rag_db_path=RAG_DB,
        extractor=lambda conn: arxiv_rag_extract.iter_docs(conn, limit=args.limit),
        chunk_fn=chunk_markdown,
        reset=args.reset,
        batch=args.batch,
        ollama_url=args.ollama_url,
        chunk_size=args.chunk_size,
        chunk_overlap=args.overlap,
        max_chunk_size=args.max_chunk_size,
        legacy_table_prefixes=("paper_chunks",),
        source_label="papers",
    )


if __name__ == "__main__":
    sys.exit(main())
