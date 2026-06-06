"""Extract one Doc per eCFR section for the RAG indexer.

Per-row Doc construction lives in `rag.ecfr.build_doc` so the API's
live-embed route can reuse it identically. This module is the indexer
entry point: it queries `regulations` and delegates to that builder.
"""

import sqlite3
from collections.abc import Iterator

from rag import Doc
from rag.ecfr import build_doc


def iter_docs(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> Iterator[Doc]:
    """Yield one Doc per row in `ecfr.regulations`.

    Args:
        conn: Read-only connection to `data/ecfr/ecfr.db`.
        limit: Maximum number of sections to yield. None processes all.
    """
    sql = (
        "SELECT id, title_num, section, heading, content "
        "FROM regulations ORDER BY id"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    for row in conn.execute(sql):
        doc = build_doc(row)
        if doc is not None:
            yield doc
