"""Shared FTS5 `OperationalError` → HTTP status translation.

When a list endpoint runs a join through `papers_fts` / `works_fts`, missing-
table errors (DB file gone, index never built) should surface as 503, while
malformed FTS5 query syntax errors from the user should surface as 400.
SQLite doesn't expose distinct error codes for these cases — only message
strings — so `rag.retriever.is_operational_error` does the discrimination.

The `/chunks` endpoints already get this dispatch via `api._chunks`; this
module provides the same translation as a context manager for the inline
FTS queries in the per-source list routes (arxiv `/papers`, openalex
`/works`).
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException

from rag.retriever import is_operational_error


@contextmanager
def translate_fts_errors(
    source_name: str,
    indexer_script: str,
    db_path: str,
) -> Iterator[None]:
    """Wrap a FTS5-joined query block; map operational vs. syntax errors.

    Args:
        source_name: Short source name; appears in the 503 detail body.
        indexer_script: Filename of the script that builds the FTS index;
            named in the 503 detail so the caller knows what to run.
        db_path: Repo-relative DB path; named in the 503 detail as a restore
            hint.

    Raises:
        HTTPException(503): When the underlying error means a missing table
            or unreadable DB file. Detail includes `indexer_script` and
            `db_path`.
        HTTPException(400): For everything else (e.g. malformed FTS5 syntax
            like an unbalanced quote).
    """
    try:
        yield
    except sqlite3.OperationalError as e:
        if is_operational_error(e):
            raise HTTPException(
                status_code=503,
                detail=(
                    f"{source_name} data not ready ({e}). "
                    f"Run scripts/{indexer_script} or restore {db_path}."
                ),
            ) from e
        # Most often a malformed FTS5 query (`q="("`, unbalanced quotes, etc.).
        raise HTTPException(status_code=400, detail=f"bad query: {e}") from e
