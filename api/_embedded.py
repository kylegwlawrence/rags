"""Shared `embedded` list-filter helper.

List endpoints expose `?embedded=true/false` to narrow results to docs that
do (or don't) have any chunks indexed in the source's `<source>_rag.db`.
The cross-reference is a simple `SELECT doc_id FROM docs_meta` against the
rag DB — `docs_meta` has one row per embedded doc and is PK-indexed on
`doc_id`, so even on the larger rag corpora the lookup stays cheap.

The id set is spliced into the main query via
`column IN (SELECT value FROM json_each(?))` with a single bound JSON
parameter, so we sidestep SQLite's 999-variable cap on positional IN-lists
(simplewiki's rag already exceeds that on its own).
"""

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException


def embedded_doc_ids(rag_opener: Callable[[], sqlite3.Connection]) -> set[str]:
    """Return the set of doc_ids with at least one chunk in this source's rag.db.

    Reads `docs_meta` (one row per indexed doc, populated by both the batch
    indexer and the live embed route). Returns an empty set when the rag DB
    isn't available — that matches "nothing is embedded" semantically and
    means a missing rag.db doesn't 503 the list endpoint, only nukes the
    filter's effect.

    Args:
        rag_opener: The source's cached read-only opener from `api.db`
            (e.g. `db.arxiv_rag`).
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

    Args:
        rag_opener: Source's cached read-only RAG opener.
        embedded: The query-param value (True or False — caller already
            short-circuited the `None` case).
        column: Fully-qualified main-table column to filter against
            (e.g. `'papers.id'`, `'works.id'`, `'articles.page_id'`).
        id_transform: Per-id transformer applied before JSON-encoding.
            Use `int` when the main column is INTEGER and the rag stores
            stringified ids (simplewiki, gutenberg). Use a prefix-prepender
            for openalex (rag has short ids, main has full URLs).

    Returns:
        `(clauses, params, is_empty)`. `is_empty` is True when
        `embedded=True` and no docs are embedded — the caller should
        short-circuit with an empty page rather than emit the clause.
        Otherwise extend the caller's `clauses` and `params` with the
        returned lists.
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
