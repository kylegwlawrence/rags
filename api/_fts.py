"""Context manager that maps SQLite OperationalError to HTTP status codes.

Missing table/DB → 503 with a hint to run the indexer script.
Bad FTS5 query syntax → 400 (when sql_error_is_user_input=True, the default).
Non-FTS SQL bugs → re-raises as 500 (sql_error_is_user_input=False).
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
    """Wrap a SQL block; translate OperationalError to 503/400/re-raise."""
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
