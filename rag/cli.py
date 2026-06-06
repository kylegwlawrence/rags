"""Shared CLI scaffolding for per-source index_rag.py scripts.
`run_index_cli` handles standard argparse flags, validation, and dispatch to `run_indexer`.
"""

import argparse
import sqlite3
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from rag import Doc, embedder
from rag.chunker import chunk_doc
from rag.indexer import run_indexer
from rag.profiles import ChunkerProfile


def run_index_cli(
    *,
    description: str | None,
    source_db_path: Path,
    rag_db_path: Path,
    extractor_factory: Callable[
        [argparse.Namespace], Callable[[sqlite3.Connection], Iterable[Doc]]
    ],
    chunk_fn: Callable[..., list[dict]] = chunk_doc,
    chunker_defaults: ChunkerProfile,
    limit_default: int | None = None,
    limit_help: str | None = None,
    source_label: str = "docs",
    legacy_table_prefixes: tuple[str, ...] = (),
    extra_meta_factory: Callable[[argparse.Namespace], dict[str, str]] | None = None,
    add_extra_args: Callable[[argparse.ArgumentParser], None] | None = None,
) -> int:
    """Parse standard rag-indexer CLI flags, validate, and dispatch to run_indexer."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--limit",
        type=int,
        default=limit_default,
        help=limit_help or (
            f"Process at most N docs (default {limit_default if limit_default is not None else 'all'})."
        ),
    )
    parser.add_argument(
        "--reset", action="store_true",
        help=f"Wipe {rag_db_path.name} and rebuild from scratch.",
    )
    parser.add_argument(
        "--batch", type=int, default=32,
        help="Embedding batch size (chunks per HTTP call).",
    )
    parser.add_argument(
        "--ollama-url", default=embedder.OLLAMA_URL,
        help="Override Ollama base URL.",
    )
    parser.add_argument(
        "--chunk-size", type=int, default=chunker_defaults.chunk_size,
        help=f"Soft target chars per chunk (default {chunker_defaults.chunk_size}).",
    )
    parser.add_argument(
        "--max-chunk-size", type=int, default=chunker_defaults.max_chunk_size,
        help=f"Hard cap on chunk length (default {chunker_defaults.max_chunk_size}).",
    )
    parser.add_argument(
        "--overlap", type=int, default=chunker_defaults.overlap,
        help=(
            f"Inter-chunk overlap in chars (default {chunker_defaults.overlap}). "
            "Overlap is within-section only when chunk_fn=chunk_markdown."
        ),
    )

    if add_extra_args is not None:
        add_extra_args(parser)

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

    extra_meta = extra_meta_factory(args) if extra_meta_factory is not None else None

    return run_indexer(
        source_db_path=source_db_path,
        rag_db_path=rag_db_path,
        extractor=extractor_factory(args),
        chunk_fn=chunk_fn,
        reset=args.reset,
        batch=args.batch,
        ollama_url=args.ollama_url,
        chunk_size=args.chunk_size,
        chunk_overlap=args.overlap,
        max_chunk_size=args.max_chunk_size,
        extra_meta=extra_meta,
        legacy_table_prefixes=legacy_table_prefixes,
        source_label=source_label,
    )


# Re-exports so per-source scripts can `from rag.cli import sys, Path` or
# similar if they want; keeps the script preamble tiny. Don't add anything
# the script doesn't reliably need.
__all__ = ["run_index_cli"]
