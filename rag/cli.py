"""Shared CLI scaffolding for `scripts/<source>/<source>_index_rag.py`.

Each wrapper script was ~80 lines of argparse + the same validations + a
single call to `rag.indexer.run_indexer`. Only a handful of pieces actually
varied: the source/rag DB paths, the extractor callable, the chunker
profile, the source-label noun, and (in a few cases) source-specific extra
flags. `run_index_cli(...)` factors out the boilerplate so each wrapper
collapses to ~25 lines.
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
    """Parse the standard rag-indexer CLI and dispatch to `run_indexer`.

    Args:
        description: Script docstring/argparse description. Usually pass `__doc__`.
        source_db_path: Read-only source SQLite DB (e.g. `data/arxiv/arxiv.db`).
        rag_db_path: Target `<source>_rag.db` to write.
        extractor_factory: Takes the parsed argparse `Namespace` and returns the
            conn-only extractor `run_indexer` expects. The factory pattern lets
            per-source CLI flags (e.g. gutenberg's `--language`) reach the
            extractor without polluting the helper's signature.
        chunk_fn: `rag.chunker.chunk_doc` (flat prose) or `chunk_markdown`
            (section-aware). Passed straight through to `run_indexer`.
        chunker_defaults: A `ChunkerProfile` from `rag.profiles`. The script
            inherits its three chunker defaults from this profile; callers
            can still override on the command line.
        limit_default: Default for `--limit`. `None` means "process every doc
            the extractor yields" — pass an int (e.g. 100) for sources where
            the full corpus is too slow on local Ollama. Validation in either
            case: if `--limit` ends up not-None, it must be positive.
        limit_help: Custom `--limit` help string (overrides the generic one,
            useful for "default 5000" or other source-specific framing).
        source_label: Noun shown in the indexer's summary print ("papers",
            "works", "articles"). Defaults to "docs".
        legacy_table_prefixes: Passed through to `run_indexer` for the wipe-
            on-detect path (arxiv uses this to detect a Phase-1 schema).
        extra_meta_factory: Builds the `extra_meta` dict from parsed args,
            e.g. `lambda a: {"source_limit": str(a.limit)}`. Returned values
            land in the rag DB's `_meta` table for run-config provenance.
        add_extra_args: Hook for per-source flags. Called after the standard
            flags are added so the source can register its own (gutenberg's
            `--language`, `--exclude-id`, `--max-pages`, etc.).

    Returns:
        Whatever `run_indexer` returns (0 on success, 1 if source DB missing).
    """
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
