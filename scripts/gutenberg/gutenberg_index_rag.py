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

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

import gutenberg_rag_extract  # noqa: E402
from rag import profiles  # noqa: E402
from rag.cli import run_index_cli  # noqa: E402

GUTENBERG_DB = REPO_ROOT / "data" / "gutenberg" / "gutenberg.db"
GUTENBERG_ROOT = REPO_ROOT / "data" / "gutenberg"
RAG_DB = REPO_ROOT / "data" / "gutenberg" / "gutenberg_rag.db"


def _add_gutenberg_args(parser) -> None:
    """Gutenberg-specific filters not relevant to other sources."""
    parser.add_argument(
        "--language", default="en",
        help="ISO language code filter (default 'en').",
    )
    parser.add_argument(
        "--exclude-id", type=int, nargs="+", default=[],
        metavar="ID", dest="exclude_ids",
        help="Gutenberg IDs to skip (e.g. --exclude-id 10 30 1581).",
    )
    parser.add_argument(
        "--max-pages", type=int, default=None,
        metavar="N", dest="max_pages",
        help="Skip texts estimated over N pages (~2000 chars/page).",
    )


if __name__ == "__main__":
    sys.exit(run_index_cli(
        description=__doc__,
        source_db_path=GUTENBERG_DB,
        rag_db_path=RAG_DB,
        extractor_factory=lambda args: (
            lambda conn: gutenberg_rag_extract.iter_docs(
                conn,
                gutenberg_root=GUTENBERG_ROOT,
                language=args.language,
                limit=args.limit,
                exclude_ids=args.exclude_ids,
                max_pages=args.max_pages,
            )
        ),
        chunker_defaults=profiles.LONG_FORM,
        limit_default=100,
        limit_help="Process at most N texts (default 100).",
        source_label="texts",
        add_extra_args=_add_gutenberg_args,
        extra_meta_factory=lambda args: {
            "source_language": args.language,
            "source_limit": str(args.limit),
        },
    ))
