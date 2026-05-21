#!/usr/bin/env python3
"""Index data/simplewiki/simplewiki.db into data/simplewiki/simplewiki_rag.db.

Each main-namespace article's raw wikitext is rendered to section-headered
markdown by `rag.wikitext.wikitext_to_markdown` and then chunked by
`rag.chunker.chunk_markdown` so each chunk carries its section heading
(e.g. "Geography", "History", "References") in the `chunks.section` column.

Default `--limit 100` mirrors gutenberg's safety-default — the full simplewiki
corpus is ~394k articles and would take many hours on local Ollama. Pass a
higher `--limit` (or remove it via `--limit 999999999`) once you are committed
to the runtime.

Re-runnable via the shared `rag.indexer.run_indexer`: docs whose
`revision_id-CLEANER_VERSION` version key matches the previously-stored
value get skipped. After this script runs, restart uvicorn so the cached
connection picks up the new file.
"""

import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import simplewiki_rag_extract  # noqa: E402
from rag import embedder  # noqa: E402
from rag.chunker import chunk_markdown  # noqa: E402
from rag.indexer import run_indexer  # noqa: E402

SIMPLEWIKI_DB = REPO_ROOT / "data" / "simplewiki" / "simplewiki.db"
RAG_DB = REPO_ROOT / "data" / "simplewiki" / "simplewiki_rag.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100,
                        help="Process at most N articles (default 100). "
                             "Full simplewiki is ~394k articles; raise explicitly.")
    parser.add_argument("--reset", action="store_true",
                        help="Wipe simplewiki_rag.db and rebuild from scratch.")
    parser.add_argument("--batch", type=int, default=32,
                        help="Embedding batch size (chunks per HTTP call).")
    parser.add_argument("--ollama-url", default=embedder.OLLAMA_URL,
                        help="Override Ollama base URL.")
    parser.add_argument("--chunk-size", type=int, default=800,
                        help="Soft target chars per chunk (default 800 ~= 200 tokens; "
                             "tuned small for accurate retrieval on small Ollama models).")
    parser.add_argument("--max-chunk-size", type=int, default=1000,
                        help="Hard cap on chunk length (default 1000).")
    parser.add_argument("--overlap", type=int, default=100,
                        help="Inter-chunk overlap in chars (default 100 ~= 12%% of chunk-size). "
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
    if args.limit < 1:
        parser.error("--limit must be a positive integer")

    return run_indexer(
        source_db_path=SIMPLEWIKI_DB,
        rag_db_path=RAG_DB,
        extractor=lambda conn: simplewiki_rag_extract.iter_docs(conn, limit=args.limit),
        chunk_fn=chunk_markdown,
        reset=args.reset,
        batch=args.batch,
        ollama_url=args.ollama_url,
        chunk_size=args.chunk_size,
        chunk_overlap=args.overlap,
        max_chunk_size=args.max_chunk_size,
        extra_meta={"source_limit": str(args.limit)},
        source_label="articles",
    )


if __name__ == "__main__":
    sys.exit(main())
