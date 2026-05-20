#!/usr/bin/env python3
"""Index data/gutenberg/gutenberg.db (plus the .txt corpus on disk) into
data/gutenberg/gutenberg_rag.db.

Filtered by language and capped by `--limit` (default `en` / 100). Full
corpus is ~38k English books and would take many hours on local Ollama —
see docs/retros for the Phase 2d scope decision.

Re-runnable via the shared `rag.indexer.run_indexer`; skips texts whose
size + first/last-4-KB fingerprint matches the previously-stored value.
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

import gutenberg_rag_extract  # noqa: E402
from rag import embedder  # noqa: E402
from rag.indexer import run_indexer  # noqa: E402

GUTENBERG_DB = REPO_ROOT / "data" / "gutenberg" / "gutenberg.db"
GUTENBERG_ROOT = REPO_ROOT / "data" / "gutenberg"
RAG_DB = REPO_ROOT / "data" / "gutenberg" / "gutenberg_rag.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100,
                        help="Process at most N texts (default 100).")
    parser.add_argument("--language", default="en",
                        help="ISO language code filter (default 'en').")
    parser.add_argument("--reset", action="store_true",
                        help="Wipe gutenberg_rag.db and rebuild from scratch.")
    parser.add_argument("--batch", type=int, default=32,
                        help="Embedding batch size (chunks per HTTP call).")
    parser.add_argument("--ollama-url", default=embedder.OLLAMA_URL,
                        help="Override Ollama base URL.")
    parser.add_argument("--chunk-size", type=int, default=2000,
                        help="Soft target chars per chunk (default 2000 — Gutenberg narrative is dense).")
    parser.add_argument("--max-chunk-size", type=int, default=2400,
                        help="Hard cap on chunk length (default 2400).")
    parser.add_argument("--overlap", type=int, default=300,
                        help="Inter-chunk overlap in chars (default 300 = 15%% of chunk-size).")
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
        source_db_path=GUTENBERG_DB,
        rag_db_path=RAG_DB,
        extractor=lambda conn: gutenberg_rag_extract.iter_docs(
            conn,
            gutenberg_root=GUTENBERG_ROOT,
            language=args.language,
            limit=args.limit,
        ),
        reset=args.reset,
        batch=args.batch,
        ollama_url=args.ollama_url,
        chunk_size=args.chunk_size,
        chunk_overlap=args.overlap,
        max_chunk_size=args.max_chunk_size,
        extra_meta={"source_language": args.language, "source_limit": str(args.limit)},
        source_label="texts",
    )


if __name__ == "__main__":
    sys.exit(main())
