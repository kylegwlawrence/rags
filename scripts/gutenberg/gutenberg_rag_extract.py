"""Extract one Doc per Gutenberg text file for the RAG indexer.

Reads each `.txt` body from disk under `data/gutenberg/<path>`, strips the
Project Gutenberg start/end banner blocks (via `rag.gutenberg_text`), and
yields the cleaned body as `Doc.text`. No section structure — the chunker
(default `chunk_doc`) splits at paragraph boundaries.

Filter defaults: `language='en'` and `--limit 100` (full corpus is ~50k
books / millions of chunks / many hours on local Ollama; the small default
proves the pipeline without committing to that runtime).

Version key combines `size_bytes` with a SHA-256 prefix of the file's first
and last 4 KB. mtime isn't reliable because the gutenberg mirror rsync can
touch every file on each sync.

The live-embed router (`api.routers.gutenberg.embed_text`) reuses the same
`read_text` / `strip_banners` / `file_fingerprint` helpers so a button-embedded
text chunks identically to a batch indexer pass.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from rag import Doc
from rag.cleaner import CLEANER_VERSION
from rag.gutenberg_text import (
    CHARS_PER_PAGE,
    file_fingerprint,
    read_text,
    strip_banners,
)


def iter_docs(
    gutenberg_conn: sqlite3.Connection,
    *,
    gutenberg_root: Path,
    language: str = "en",
    limit: int = 100,
    exclude_ids: list[int] | None = None,
    max_pages: int | None = None,
) -> Iterator[Doc]:
    """Yield one Doc per Gutenberg text matching `language`, capped to `limit`.

    Args:
        gutenberg_conn: Read-only connection to `data/gutenberg/gutenberg.db`.
        gutenberg_root: On-disk root for resolving `texts.path`
            (`data/gutenberg/`).
        language: ISO language code to filter on. Default `en`.
        limit: Max number of texts to yield, ordered by `texts.id`.
        exclude_ids: Gutenberg text IDs to skip entirely.
        max_pages: Skip texts whose estimated page count (size_bytes /
            CHARS_PER_PAGE) exceeds this value.
    """
    excluded = exclude_ids or []
    params: list = [language]

    clauses = ["language = ?"]
    if excluded:
        placeholders = ",".join("?" * len(excluded))
        clauses.append(f"id NOT IN ({placeholders})")
        params.extend(excluded)
    if max_pages is not None:
        clauses.append("size_bytes <= ?")
        params.append(max_pages * CHARS_PER_PAGE)

    where = " AND ".join(clauses)
    params.append(limit)
    cursor = gutenberg_conn.execute(
        f"SELECT id, title, author, path FROM texts WHERE {where} ORDER BY id LIMIT ?",
        params,
    )
    for row in cursor:
        path = gutenberg_root / row["path"]
        if not path.is_file():
            continue  # rsync may not have pulled every file; skip silently
        body = strip_banners(read_text(path))
        if not body:
            continue
        title = row["title"] or row["author"] or str(row["id"])
        yield Doc(
            doc_id=str(row["id"]),
            title=title,
            version=f"{file_fingerprint(path)}-{CLEANER_VERSION}",
            text=body,
            section=None,
        )
