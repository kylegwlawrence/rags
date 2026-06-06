"""Shared `?embedded=` filter helper for list endpoints.

Reads `docs_meta` from the source's rag DB (one row per indexed doc). Splices
the id set into the main query via `json_each(?)` to sidestep SQLite's 999-
variable cap on positional IN-lists.
"""

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException


def embedded_doc_ids(rag_opener: Callable[[], sqlite3.Connection]) -> set[str]:
    """Return doc_ids with at least one chunk in the rag DB.

    Returns an empty set if the rag DB is missing — degrades to "nothing embedded"
    rather than 503-ing the list endpoint.
    """
    try:
        conn = rag_opener()
    except HTTPException:
        return set()
    try:
        return {r[0] for r in conn.execute("SELECT doc_id FROM docs_meta")}
    except sqlite3.OperationalError:
        # rag.db opened but `docs_meta` is missing (legacy schema or freshly
        # rebuilt mid-request). Treat as empty so the filter degrades to
        # "nothing embedded" rather than crashing the list endpoint.
        return set()


def embedded_clauses(
    rag_opener: Callable[[], sqlite3.Connection],
    *,
    embedded: bool,
    column: str,
    id_transform: Callable[[str], Any] = lambda s: s,
) -> tuple[list[str], list[Any], bool]:
    """Build WHERE fragments + bind params for the `?embedded=` filter.

    Returns `(clauses, params, is_empty)`. When `is_empty` is True the caller
    should short-circuit with an empty page. Pass `id_transform=int` when the
    main column is INTEGER (simplewiki, gutenberg) or a prefix-prepender for
    openalex (rag stores short ids, main table has full URLs).
    """
    ids = [id_transform(d) for d in embedded_doc_ids(rag_opener)]
    if embedded:
        if not ids:
            return [], [], True
        return (
            [f"{column} IN (SELECT value FROM json_each(?))"],
            [json.dumps(ids)],
            False,
        )
    # embedded=False
    if not ids:
        # Nothing is embedded → every row qualifies as "unembedded", no clause.
        return [], [], False
    return (
        [f"{column} NOT IN (SELECT value FROM json_each(?))"],
        [json.dumps(ids)],
        False,
    )
