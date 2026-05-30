"""Extract one Doc per EUR-Lex law for the RAG indexer.

Per-row Doc construction lives in `rag.eurlex.build_doc` so the API's
live-embed route can reuse it identically. This module is the indexer entry
point: it queries `laws` and delegates to that builder.
"""

import sqlite3
from collections.abc import Iterator

from rag import Doc
from rag.eurlex import build_doc


def iter_docs(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> Iterator[Doc]:
    """Yield one Doc per `laws` row with a non-empty body, newest first.

    Args:
        conn: Read-only connection to `data/eurlex/eurlex.db`.
        limit: Maximum number of laws to yield. None processes all.
    """
    sql = (
        "SELECT CELEX, Act_name, act_raw_text FROM laws "
        "WHERE act_raw_text IS NOT NULL AND act_raw_text != '' "
        "ORDER BY Date_publication DESC, CELEX DESC"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    for row in conn.execute(sql):
        doc = build_doc(row)
        if doc is not None:
            yield doc
