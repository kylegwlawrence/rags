"""Shared SQLite `OperationalError` → HTTP status translation.

Two kinds of operational errors look similar to SQLite but mean very different
things to a route handler:

* "no such table" / "unable to open database file" → the indexer hasn't run
  or the DB file is gone. The right response is 503 with a hint about which
  script to run.
* Bad FTS5 query syntax (`q="("`, unbalanced quotes) → the user typed
  something the FTS tokenizer can't parse. The right response is 400.

`rag.retriever.is_operational_error` discriminates these by message string;
this module wraps it as a context manager so per-source list routes can run
the same dispatch with one `with` block.

By default the wrapper is FTS-aware — non-table errors map to 400. The
`sql_error_is_user_input=False` form is for non-FTS joins where any bad SQL
is the codebase's fault, not the caller's (e.g. arxiv's `paper_authors`
join), and re-raises so it surfaces as a 500.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException

from rag.retriever import is_operational_error


@contextmanager
def translate_table_errors(
    source_name: str,
    indexer_script: str,
    db_path: str,
    *,
    sql_error_is_user_input: bool = True,
) -> Iterator[None]:
    """Wrap a SQL query block; map missing-table vs. other operational errors.

    Args:
        source_name: Short source name; appears in the 503 detail body.
        indexer_script: Filename of the script that builds the index/table;
            named in the 503 detail so the caller knows what to run.
        db_path: Repo-relative DB path; named in the 503 detail as a restore
            hint.
        sql_error_is_user_input: When True (default — FTS sites), non-table
            errors map to 400 because they're almost always malformed user
            FTS syntax. When False (non-FTS joins like arxiv's
            paper_authors), they re-raise so they surface as 500 — a bug in
            the SQL is on us, not the caller.

    Raises:
        HTTPException(503): The underlying error means a missing table or
            unreadable DB file. Detail includes `indexer_script` and `db_path`.
        HTTPException(400): When `sql_error_is_user_input=True` and the
            error isn't a missing table (e.g. malformed FTS5 syntax).
        sqlite3.OperationalError: When `sql_error_is_user_input=False` and
            the error isn't a missing table — re-raised unchanged.
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
        if sql_error_is_user_input:
            # Most often a malformed FTS5 query (`q="("`, unbalanced quotes).
            raise HTTPException(status_code=400, detail=f"bad query: {e}") from e
        raise  # codebase bug, not user input — let it surface as 500
